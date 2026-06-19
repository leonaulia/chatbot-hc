import streamlit as st

# Secrets
GROQ_API_KEY = st.secrets["GROQ_API_KEY"]
DB_URL = st.secrets["DB_URL"]

# ========================================= DATABASE =====================================================

# Connect DB
engine = (create_engine(DB_URL))
# check connection
if engine:
    print("connected supabase")
else:
    print("connected failed")

@st.cache_resource
def get_engine():
    eng = create_engine(DB_URL, pool_pre_ping=True)
    return eng

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


