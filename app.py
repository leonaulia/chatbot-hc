import streamlit as st
import os, re, json, time, hashlib
from datetime import date, timedelta
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

# Set up page configuration first before any other Streamlit commands
st.set_page_config(page_title='Conversational Analytics — HR', page_icon='📊', layout='centered')

# Secrets
GROQ_API_KEY = st.secrets.get("GROQ_API_KEY", "")
DB_URL = st.secrets.get("DB_URL", "postgresql://localhost/postgres")

# ========================================= DATABASE =====================================================

@st.cache_resource
def get_engine():
    # Use pool_pre_ping to automatically recover from stale/dropped connections
    return create_engine(DB_URL, pool_pre_ping=True)

engine = get_engine()

# DB related prompt properties
DIALEK = "PostgreSQL"
SKEMA = '''
employees(nip PK, nama, divisi, jabatan, join_date)
trainings(training_id PK, nama_diklat, tanggal)
enrollments(
    nip PK FK REFERENCES employees(nip), 
    training_id PK FK REFERENCES trainings(training_id), 
    status, 
    nilai
)
'''
TABEL_OK = {'employees', 'trainings', 'enrollments'}
TERLARANG = ('drop', 'delete', 'update', 'insert', 'alter', 'truncate', 'create', 'replace',
             'grant', 'revoke', 'merge', 'into', 'attach', 'detach', 'pragma', 'vacuum', 'copy', 'dblink')
POLA_BAHAYA = (r'\binformation_schema\b', r'\bpg_catalog\b', r'\bpg_\w+\b',
               r'\bsqlite_master\b', r'\bload_extension\b', r'\blo_import\b', r'\blo_export\b')
_FUNGSI_FROM = r'\b(?:extract|substring|trim|position|overlay)\s*\([^)]*\)'
PROMPT_VERSION = 'v1.0'


#======================================= LLM setup =================================================
OPENAI_MODEL = 'llama-3.3-70b-versatile'
PROVIDER = 'GROQ'  # Options: 'gemini', 'openai', 'ollama', 'GROQ'

def panggil_openai(prompt, temperature=0, max_tokens=1024, top_p=1.0,
                   frequency_penalty=0.0, presence_penalty=0.0):
    from openai import OpenAI
    # Fallback directly to Streamlit secrets if environment variables aren't set
    api_key = os.environ.get('GROQ_API_KEY') or os.environ.get('OPENAI_API_KEY') or GROQ_API_KEY
    client = OpenAI(api_key=api_key, base_url='https://api.groq.com/openai/v1')
    
    response = client.chat.completions.create(
        model=OPENAI_MODEL, temperature=temperature, max_tokens=max_tokens, top_p=top_p,
        frequency_penalty=frequency_penalty, presence_penalty=presence_penalty,
        messages=[{'role': 'user', 'content': prompt}]
    )
    return response.choices[0].message.content

def _mock_llm(prompt):
    return "SELECT * FROM employees LIMIT 5;"

# Router function
def tanya_llm(prompt, temperature=0, **kwargs):
    if PROVIDER == 'openai' or PROVIDER == 'GROQ':
        return panggil_openai(prompt, temperature, **kwargs)
    # Stubs for other providers if you build them later:
    # if PROVIDER == 'gemini': return panggil_gemini(prompt, temperature, **kwargs)
    # if PROVIDER == 'ollama': return panggil_ollama(prompt, temperature, **kwargs)
    return _mock_llm(prompt)

#======================================= Functions =================================================
def bangun_prompt(pertanyaan):
    return (f'Anda ahli SQL {DIALEK}. Skema:\n{SKEMA}\n'
            'Buat SATU query SELECT (JOIN bila perlu). Balas HANYA query SQL.\n'
            f'Pertanyaan: {pertanyaan}')


def ambil_sql(resp):
    if isinstance(resp, dict):
        resp = resp.get('sql', json.dumps(resp))
    teks = str(resp).strip()
    m = re.search(r'```(?:sql)?\s*(.+?)```', teks, re.S)
    if m:
        teks = m.group(1).strip()
    m = re.search(r'(select\b.+)', teks, re.I | re.S)
    if m:
        teks = m.group(1)
    return teks.rstrip(';').strip()


def _strip_komentar(sql):
    sql = re.sub(r'/\*.*?\*/', ' ', sql, flags=re.S)
    return re.sub(r'--[^\n]*', ' ', sql)


def validasi_sql(sql, batas=200, batas_maks=1000):
    t = _strip_komentar(sql).strip().rstrip(';').strip()
    low = t.lower()
    if not (low.startswith('select') or low.startswith('with')):
        raise ValueError('Hanya SELECT/WITH')
    if ';' in t:
        raise ValueError('Multi-statement')
    for k in TERLARANG:
        if re.search(rf'\b{k}\b', low):
            raise ValueError(f'Terlarang: {k}')
    for pola in POLA_BAHAYA:
        m = re.search(pola, low)
        if m:
            raise ValueError(f'Objek terlarang: {m.group()}')
    low_tab = re.sub(_FUNGSI_FROM, ' ', low)
    asing = set(re.findall(r'(?:from|join)\s+([a-zA-Z_][a-zA-Z0-9_]*)', low_tab)) - TABEL_OK
    if asing:
        raise ValueError(f'Tabel tak dikenal: {asing}')
    m = re.search(r'\blimit\s+(\d+)', low)
    if m:
        if int(m.group(1)) > batas_maks:
            t = re.sub(r'\blimit\s+\d+', f'LIMIT {batas_maks}', t, flags=re.I)
    else:
        t += f' LIMIT {batas}'
    return t


def ask_db(pertanyaan, maks_retry=2):
    prompt = bangun_prompt(pertanyaan)
    last = None
    for _ in range(maks_retry + 1):
        sql = ambil_sql(tanya_llm(prompt, temperature=0))
        try:
            sql_aman = validasi_sql(sql)
        except ValueError as e:
            last = f'validasi: {e}'
            prompt = bangun_prompt(pertanyaan) + f'\nSQL gagal: {sql}\nERROR: {last}\nPerbaiki.'
            continue
        try:
            with engine.connect() as c:
                df = pd.read_sql(text(sql_aman), c)
            return {'ok': True, 'sql': sql_aman, 'data': df}
        except Exception as e:
            last = str(e)
            prompt = bangun_prompt(pertanyaan) + f'\nSQL gagal: {sql_aman}\nERROR: {last}\nPerbaiki.'
    return {'ok': False, 'error': last, 'fallback': 'Maaf, query valid tidak dapat disusun.'}


# ── Routing & format ───────────────────────────────────────────────
def pilih_format(pertanyaan, df=None):
    p = pertanyaan.lower()
    if any(k in p for k in ['grafik', 'chart', 'visual', 'plot', 'diagram', 'pie', 'bar chart', 'line chart']):
        return 'chart'
    if any(k in p for k in ['kenapa', 'mengapa', 'insight', 'jelaskan', 'analisis', 'narasi', 'ceritakan']):
        return 'narasi'
    if 'json' in p or 'api' in p or 'dashboard' in p:
        return 'json'
    return 'tabel'


def _ringkas_df(df):
    if df is None or len(df) == 0:
        return 'tidak ada data'
    cols = list(df.columns)
    if len(cols) >= 2 and pd.api.types.is_numeric_dtype(df[cols[-1]]):
        top = df.iloc[0]
        return f"'{top[cols[0]]}' tertinggi pada {cols[-1]} = {top[cols[-1]]:,.0f}; {len(df)} baris"
    return f'{len(df)} baris; kolom: ' + ', '.join(cols)


def buat_narasi(df, pertanyaan):
    fakta = _ringkas_df(df)
    prompt = (f'Anda analis data. Pertanyaan: {pertanyaan}\n'
              f'Data:\n{df.head(10).to_string(index=False)}\n'
              f'RINGKAS_DATA: {fakta}\n'
              'Tulis narasi 2-3 kalimat berbasis data di atas saja.')
    return tanya_llm(prompt)


def format_json(df, pertanyaan):
    return {'format': 'json', 'pertanyaan': pertanyaan, 'kolom': list(df.columns),
            'jumlah_baris': int(len(df)), 'data': df.head(50).to_dict(orient='records'),
            'ringkasan': _ringkas_df(df)}


def pilih_jenis_chart(pertanyaan, df):
    p, x = pertanyaan.lower(), str(df.columns[0]).lower()
    if 'pie' in p or 'komposisi' in p or 'proporsi' in p:
        return 'pie'
    if 'periode' in x or 'bulan' in p or 'tren' in p:
        return 'line'
    return 'bar'


TEAL = '#0E8388'


def buat_chart(df, pertanyaan='', jenis=None):
    x, y = df.columns[0], df.columns[-1]
    jenis = jenis or pilih_jenis_chart(pertanyaan, df)
    fig, ax = plt.subplots(figsize=(7, 4))
    if jenis == 'line':
        ax.plot(df[x].astype(str), df[y], marker='o', color=TEAL)
        ax.set_ylabel(str(y)); plt.xticks(rotation=30, ha='right')
    elif jenis == 'pie':
        ax.pie(df[y], labels=df[x].astype(str), autopct='%1.0f%%',
               colors=plt.cm.Greens([0.4, 0.55, 0.7, 0.85, 0.6, 0.45]))
    else:
        ax.bar(df[x].astype(str), df[y], color=TEAL)
        ax.set_ylabel(str(y)); plt.xticks(rotation=30, ha='right')
    ax.set_title(pertanyaan or f'{y} per {x}')
    plt.tight_layout()
    return fig


def jawab(pertanyaan, force=None):
    res = ask_db(pertanyaan)
    if not res['ok']:
        return {'format': 'error', 'isi': res.get('fallback'), 'pertanyaan': pertanyaan}
    df = res['data']
    fmt = force or pilih_format(pertanyaan, df)
    out = {'format': fmt, 'sql': res['sql'], 'df': df, 'pertanyaan': pertanyaan}
    if fmt == 'narasi':
        out['isi'] = buat_narasi(df, pertanyaan)
    elif fmt == 'json':
        out['isi'] = format_json(df, pertanyaan)
    else:
        out['isi'] = None  # tabel & chart dirender dari df
    return out


# ── UI Streamlit ───────────────────────────────────────────────────
st.set_page_config(page_title='Conversational Analytics — HR', page_icon='📊', layout='centered')
st.title('Conversational Analytics — HR')
st.caption('Tanya data HR dalam bahasa biasa → jawaban adaptif (tabel / narasi / JSON / chart).')

with st.sidebar:
    st.subheader('⚙️ Konfigurasi')
    st.write(f'**Provider:** `{PROVIDER}`')
    st.write(f'**Database:** `{DIALEK}`')
    paksa = st.selectbox('Paksa format', ['auto', 'tabel', 'narasi', 'json', 'chart'])
    st.markdown('**Contoh:**')
    for ex in ["Berapa jumlah pegawai per divisi?",
            "Siapa yang belum mengikuti diklat Data Engineering?",
            "Berapa rata-rata nilai diklat per unit (divisi)?"]:
        st.caption('• ' + ex)
    if st.button('🗑️ Bersihkan chat'):
        st.session_state.messages = []
        st.rerun()

if 'messages' not in st.session_state:
    st.session_state.messages = []
if 'cache' not in st.session_state:
    st.session_state.cache = {}


def _render(payload):
    fmt = payload['format']
    if fmt == 'error':
        st.error(payload['isi']); return
    if payload.get('sql'):
        with st.expander('🔎 SQL'):
            st.code(payload['sql'], language='sql')
    if fmt == 'tabel':
        st.dataframe(payload['df'], use_container_width=True)
    elif fmt == 'narasi':
        st.write(payload['isi'])
    elif fmt == 'json':
        st.json(payload['isi'])
    elif fmt == 'chart':
        st.pyplot(buat_chart(payload['df'], payload['pertanyaan']))


for m in st.session_state.messages:
    with st.chat_message(m['role']):
        if m['role'] == 'user':
            st.markdown(m['content'])
        else:
            _render(m['payload'])

q = st.chat_input('Tanya tentang data HR…')
if q:
    st.session_state.messages.append({'role': 'user', 'content': q})
    key = hashlib.sha256((q.lower().strip() + '|' + paksa).encode()).hexdigest()
    with st.spinner('Memproses…'):
        if key in st.session_state.cache:
            out = st.session_state.cache[key]
        else:
            out = jawab(q, force=None if paksa == 'auto' else paksa)
            st.session_state.cache[key] = out
    st.session_state.messages.append({'role': 'assistant', 'payload': out})
    st.rerun()