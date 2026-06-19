import json
from io import BytesIO
import re
import unicodedata
import dateutil.parser
import gspread
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from google.oauth2.service_account import Credentials
from openai import OpenAI


# =============================================================================
# CONFIGURATION
# =============================================================================

SPREADSHEET_URL = "https://docs.google.com/spreadsheets/d/1fVwqc6T3VugBZrW1f4A8bfajnpETo1UEmt8Ren7a9hY/edit"
SHEET_NAME = "Input Utama"

TAB_LIST = ["Aktif", "Analytics", "Report", "Gaji"]
STATUS_OPTIONS = ["All", "Selesai", "Batal", "Paused", "Upcoming", "Aktif"]
SUB_BIDANG_CODES = ["SUP", "PDT", "AGAPro", "DDR"]
SUB_BIDANG_OPTIONS = [
    "All",
    "Support",
    "Pembangkit dan Transmisi",
    "Niaga Proyek",
    "Distribusi dan Ritel",
]
SUB_BIDANG_MAPPING = {
    "Support": "SUP",
    "Pembangkit dan Transmisi": "PDT",
    "Niaga Proyek": "AGAPro",
    "Distribusi dan Ritel": "DDR",
}
SUB_BIDANG_LABELS = {
    "SUP": "Support",
    "PDT": "Pembangkit dan Transmisi",
    "AGAPro": "Niaga Proyek",
    "DDR": "Distribusi dan Ritel",
}
ITBP_TEAM_SIZE = {
    "SUP": 4,
    "PDT": 2,
    "DDR": 2,
    "AGAPro": 3,
}

SLA_TARGETS = {
    "CR Mayor": 12.72,
    "CR Minor": 5.28,
    "CR Darurat": 1.0,
    "JR Mayor": 13.0,
    "JR Minor": 13.0,
}
KPI_LABELS = ["CR Mayor", "CR Minor", "CR Darurat", "JR Mayor", "JR Minor"]
REPORT_COLUMNS = [
    "Bulan",
    "Tanggal Masuk",
    "No Nodin Masuk",
    "Judul CR",
    "Aplikasi",
    "SLA terpakai",
    "BPO",
    "Tanggal Nodin kembali ke BPO",
    "Tanggal Nodin ke MDG",
    "Tanggal Nodin Pelaksanaan",
    "No Nodin Balasan ke BPO",
    "No Nodin Balasan ke MDG",
    "No Nodin Pelaksanaan",
]
INDONESIAN_MONTHS = {
    1: "Januari",
    2: "Februari",
    3: "Maret",
    4: "April",
    5: "Mei",
    6: "Juni",
    7: "Juli",
    8: "Agustus",
    9: "September",
    10: "Oktober",
    11: "November",
    12: "Desember",
}

ANALYTICS_COLOR_MAP = {
    "Batal": "#854D0E",
    "Selesai": "#166534",
    "Paused": "#4B5563",
    "Aktif": "#FFC000",
    "Upcoming": "#1D4ED8",
}

ACTIVE_AI_TOOLS = [
    {
        "type": "function",
        "name": "get_sla_summary",
        "description": (
            "Menghitung pencapaian SLA untuk CR Mayor, CR Minor, CR Darurat, "
            "JR Mayor, dan JR Minor dari request berstatus Selesai. Jika tahun "
            "tidak diberikan, gunakan tahun yang sedang dipilih di dashboard."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "year": {"type": ["integer", "null"]},
                "request_types": {
                    "type": "array",
                    "items": {"type": "string", "enum": KPI_LABELS},
                },
            },
        },
    },
    {
        "type": "function",
        "name": "rank_groups",
        "description": (
            "Membuat peringkat berdasarkan aplikasi, BPO, Sub-Bidang, PIC, "
            "atau jenis request. Dapat menghitung jumlah, rata-rata, median, "
            "atau maksimum SLA terpakai. Pencarian aplikasi mencakup Nama "
            "Aplikasi dan Aplikasi Terdampak."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "year": {"type": ["integer", "null"]},
                "group_by": {
                    "type": "string",
                    "enum": [
                        "application",
                        "bpo",
                        "sub_bidang",
                        "pic",
                        "request_type",
                    ],
                },
                "metric": {
                    "type": "string",
                    "enum": [
                        "count",
                        "average_sla",
                        "median_sla",
                        "maximum_sla",
                    ],
                },
                "statuses": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "jenis": {
                    "type": ["string", "null"],
                    "enum": ["CR", "JR", None],
                },
                "request_types": {
                    "type": "array",
                    "items": {"type": "string", "enum": KPI_LABELS},
                },
                "application": {"type": ["string", "null"]},
                "minimum_count": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20,
                },
                "sort_order": {
                    "type": "string",
                    "enum": ["ascending", "descending"],
                },
            },
            "required": ["group_by", "metric"],
        },
    },
    {
        "type": "function",
        "name": "find_requests",
        "description": (
            "Mencari dan mengurutkan detail CR/JR. Gunakan untuk mencari request "
            "terlama, request yang near SLA atau over SLA, pencarian aplikasi, "
            "filter BPO, dan "
            "pertanyaan progres atau status request. "
            "Status CR/JR ada 5, yaitu: Upcoming, Aktif, Selesai, Batal, Paused."
            "Hasil menyertakan Progres dan Keterangan."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "year": {"type": ["integer", "null"]},
                "statuses": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "jenis": {
                    "type": ["string", "null"],
                    "enum": ["CR", "JR", None],
                },
                "request_types": {
                    "type": "array",
                    "items": {"type": "string", "enum": KPI_LABELS},
                },
                "application": {"type": ["string", "null"]},
                "bpo": {"type": ["string", "null"]},
                "sla_alert_statuses": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["Over SLA", "Near SLA", "Safe", "Not Active"],
                    },
                },
                "sort_by": {
                    "type": "string",
                    "enum": [
                        "sla_terpakai",
                        "sisa_sla",
                        "tanggal_masuk",
                    ],
                },
                "sort_order": {
                    "type": "string",
                    "enum": ["ascending", "descending"],
                },
                "max_rows": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20,
                },
            },
        },
    },
]

APPLICATION_ALIASES = {
    "ibm maximo": "Maximo",
    "sap ecc": "SAP",
    "sap erp": "SAP",
}
APPLICATION_CANONICAL_NAMES = {
    "maximo": "Maximo",
    "sap": "SAP",
}


# =============================================================================
# CLIENTS AND STATE
# =============================================================================

def get_openai_client():
    try:
        return OpenAI()
    except Exception:
        return None


def initialize_session_state():
    defaults = {
        "filter_sub_bidang": "All",
        "dashboard_year": 2026,
        "analytics_status_filter": ["Selesai"],
        "analytics_sub_filter": ["All"],
        "itbp_jenis_filter": ["All"],
        "itbp_klasifikasi_filter": ["All"],
        "analytics_selected_bpo": None,
        "bpo_chart_version": 0,
        "seasonality_sub_filter": ["All"],
        "chat_history": [
            {
                "role": "assistant",
                "content": (
                    "Halo bos! Ada yang bisa saya bantu? Kamu bisa bertanya tentang data CR/JR"
                ),
            }
        ],
    }

    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


# =============================================================================
# DATA ACCESS
# =============================================================================

@st.cache_data(ttl=600)
def load_data(spreadsheet_url, sheet_name="Sheet1"):
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.metadata.readonly",
    ]
    creds = Credentials.from_service_account_file("creds.json", scopes=scopes)
    client_gs = gspread.authorize(creds)

    spreadsheet = client_gs.open_by_url(spreadsheet_url)
    raw_time = spreadsheet.get_lastUpdateTime()
    parsed_time = dateutil.parser.isoparse(raw_time)
    formatted_metadata_time = parsed_time.strftime("%d %B %Y, %H:%M WIB")

    sheet = spreadsheet.worksheet(sheet_name)
    records = sheet.get_all_records(head=2)

    return pd.DataFrame(records), formatted_metadata_time


# =============================================================================
# DATA PREPARATION
# =============================================================================

def prepare_base_data(df):
    if df.empty:
        return pd.DataFrame()

    prepared_df = df.copy()
    execution_date = pd.to_datetime(
        prepared_df["Tanggal Nodin Pelaksanaan"],
        dayfirst=True,
        errors="coerce",
    )
    entry_date = pd.to_datetime(
        prepared_df["Tanggal Masuk"],
        dayfirst=True,
        errors="coerce",
    )
    returned_date = pd.to_datetime(
        prepared_df["Tanggal Nodin kembali ke BPO"],
        dayfirst=True,
        errors="coerce",
    )

    reporting_date = entry_date.copy()
    reporting_date.loc[prepared_df["Status"] == "Selesai"] = execution_date
    reporting_date.loc[prepared_df["Status"] == "Batal"] = entry_date
    reporting_date.loc[prepared_df["Status"] == "Paused"] = returned_date

    prepared_df["Tanggal Laporan"] = reporting_date
    prepared_df["Tahun Laporan"] = reporting_date.dt.year.astype("Int64")

    return prepared_df


def prepare_metrics_data(df, selected_year):
    return df[df["Tahun Laporan"] == selected_year].copy()


def prepare_active_data(df):
    if df.empty:
        return pd.DataFrame()

    active_df = df[df["Status"] == "Aktif"].copy()
    if active_df.empty:
        return active_df

    app_name = active_df["Nama Aplikasi"].astype(str).str.strip().fillna("")
    impacted_app = (
        active_df["Aplikasi Terdampak"].astype(str).str.strip().fillna("")
    )

    active_df["Aplikasi"] = np.where(
        (app_name != "") & (impacted_app != ""),
        app_name + " / " + impacted_app,
        app_name.str.cat(impacted_app, sep=""),
    )

    category = (
        active_df["Jenis"].astype(str).str.strip().fillna("")
        + " "
        + active_df["Klasifikasi"].astype(str).str.strip().fillna("")
    )
    active_df["Kategori"] = category.str.strip()

    return active_df


def prepare_analytics_data(df, selected_year):
    if df.empty:
        return pd.DataFrame()

    analytics_df = df[df["Tahun Laporan"] == selected_year].copy()
    analytics_df["BPO"] = analytics_df["BPO"].replace("DIV OKI", "DIV OPP")

    return analytics_df


def filter_analytics_data(analytics_df, selected_statuses, selected_sub):
    if "All" in selected_sub or not selected_sub:
        filtered_df = analytics_df.copy()
    else:
        target_codes = [
            SUB_BIDANG_MAPPING[sub]
            for sub in selected_sub
            if sub in SUB_BIDANG_MAPPING
        ]
        if target_codes:
            filtered_df = analytics_df[
                analytics_df["Sub-Bidang"]
                .astype(str)
                .str.contains("|".join(target_codes), na=False)
            ]
        else:
            filtered_df = analytics_df.copy()

    if "All" in selected_statuses or not selected_statuses:
        filtered_df = filtered_df[
            filtered_df["Status"].isin(STATUS_OPTIONS)
        ].copy()
    else:
        filtered_df = filtered_df[
            filtered_df["Status"].isin(selected_statuses)
        ].copy()

    return filtered_df


# =============================================================================
# BUSINESS CALCULATIONS
# =============================================================================

def summarize_kpis(df):
    done_df = df[df.get("Status") == "Selesai"].copy()
    sla_col = "SLA terpakai"

    if sla_col in done_df.columns:
        done_df[sla_col] = pd.to_numeric(done_df[sla_col], errors="coerce")

    sla_count_group = {}
    for jenis in ["CR", "JR"]:
        for klasifikasi in ["Mayor", "Minor", "Darurat"]:
            if jenis == "JR" and klasifikasi == "Darurat":
                continue

            subset = done_df[
                (done_df["Jenis"] == jenis)
                & (done_df["Klasifikasi"] == klasifikasi)
            ]

            avg_value = (
                subset[sla_col].mean()
                if sla_col in subset.columns and not subset.empty
                else None
            )
            metric_key = f"{jenis} {klasifikasi}"
            sla_count_group[metric_key] = (avg_value, len(subset))

    return sla_count_group


def calculate_urgent_count(active_df):
    return pd.to_numeric(
        active_df["Sisa SLA"],
        errors="coerce",
    ).between(0, 3, inclusive="both").sum()

def calculate_exurgent_count(active_df):
    return pd.to_numeric(
        active_df["Sisa SLA"],
        errors="coerce",
    ).lt(0).sum()


def build_itbp_analytics_data(
    analytics_df,
    selected_request_type,
):
    source_df = analytics_df[
        analytics_df["Status"] == "Selesai"
    ].copy()

    if selected_request_type != "All":
        selected_jenis, selected_klasifikasi = selected_request_type.split(
            " ", 1
        )
        source_df = source_df[
            (source_df["Jenis"] == selected_jenis)
            & (source_df["Klasifikasi"] == selected_klasifikasi)
        ]

    expanded_rows = []
    for code in SUB_BIDANG_CODES:
        team_rows = source_df[
            source_df["Sub-Bidang"]
            .astype(str)
            .str.contains(code, na=False)
        ]
        for _, row in team_rows.iterrows():
            expanded_rows.append(
                {
                    "Sub-Bidang": code,
                    "Jenis": row["Jenis"],
                    "Klasifikasi": row["Klasifikasi"],
                    "Request Type": (
                        f"{row['Jenis']} {row['Klasifikasi']}"
                    ),
                    "Judul CR": row["Judul CR"],
                    "SLA terpakai": row["SLA terpakai"],
                }
            )

    if not expanded_rows:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    expanded_df = pd.DataFrame(expanded_rows)
    expanded_df["SLA terpakai"] = pd.to_numeric(
        expanded_df["SLA terpakai"],
        errors="coerce",
    )
    request_counts = (
        expanded_df.groupby(["Sub-Bidang", "Jenis"])
        .size()
        .reset_index(name="Jumlah CR/JR")
    )
    visible_jenis = (
        ["CR", "JR"]
        if selected_request_type == "All"
        else [selected_request_type.split(" ", 1)[0]]
    )
    request_counts = (
        request_counts.set_index(["Sub-Bidang", "Jenis"])
        .reindex(
            pd.MultiIndex.from_product(
                [SUB_BIDANG_CODES, visible_jenis],
                names=["Sub-Bidang", "Jenis"],
            ),
            fill_value=0,
        )
        .reset_index()
    )
    team_summary = (
        expanded_df.groupby("Sub-Bidang")
        .agg(
            Total_Request=("Jenis", "size"),
            Rata_Rata_SLA=("SLA terpakai", "mean"),
        )
        .reindex(SUB_BIDANG_CODES)
        .reset_index()
    )
    team_summary["Total_Request"] = (
        team_summary["Total_Request"].fillna(0).astype(int)
    )
    team_summary["Jumlah_Person"] = (
        team_summary["Sub-Bidang"].map(ITBP_TEAM_SIZE)
    )
    team_summary["Request_per_Person"] = (
        team_summary["Total_Request"]
        / team_summary["Jumlah_Person"]
    )

    return request_counts, team_summary, expanded_df


def build_analytics_counts(filtered_df):
    chart_source = filtered_df.copy()
    chart_source["BPO"] = chart_source["BPO"].astype(str).str.strip()
    chart_source = chart_source[
        ~chart_source["BPO"].isin(["", "-", "nan", "None"])
    ]

    all_bpo_names_sorted = chart_source["BPO"].value_counts().index.tolist()
    chart_data = chart_source[
        chart_source["BPO"].isin(all_bpo_names_sorted)
    ].copy()
    stacked_counts = (
        chart_data.groupby(["BPO", "Status"])
        .size()
        .reset_index(name="Jumlah CR/JR")
    )
    total_counts = (
        chart_data.groupby("BPO")
        .size()
        .reset_index(name="Total")
    )

    return stacked_counts, total_counts, all_bpo_names_sorted


def build_application_counts(filtered_df, selected_bpo=None):
    chart_source = filtered_df.copy()
    chart_source["BPO"] = chart_source["BPO"].astype(str).str.strip()
    if selected_bpo:
        chart_source = chart_source[chart_source["BPO"] == selected_bpo]

    app_name = (
        chart_source["Nama Aplikasi"]
        .fillna("")
        .astype(str)
        .str.strip()
    )
    impacted_app = (
        chart_source["Aplikasi Terdampak"]
        .fillna("")
        .astype(str)
        .str.strip()
    )
    chart_source["Aplikasi"] = app_name.where(
        app_name.ne(""),
        impacted_app,
    )
    chart_source = chart_source[
        ~chart_source["Aplikasi"].isin(["", "-", "nan", "None"])
    ]
    # 2. Split the string into a list using the delimiter
    chart_source['Aplikasi'] = chart_source['Aplikasi'].str.split(', ')
    chart_source = chart_source.explode("Aplikasi")
    chart_source["Aplikasi"] = (
        chart_source["Aplikasi"].astype(str).str.strip()
    )
    chart_source["Request Type"] = (
        chart_source["Jenis"].astype(str).str.strip()
        + " "
        + chart_source["Klasifikasi"].astype(str).str.strip()
    ).str.strip()
    valid_request_types = [
        "CR Mayor",
        "CR Minor",
        "CR Darurat",
        "JR Mayor",
        "JR Minor",
    ]
    chart_source = chart_source[
        chart_source["Request Type"].isin(valid_request_types)
    ]

    application_counts = (
        chart_source.groupby(["Aplikasi", "Request Type"])
        .size()
        .reset_index(name="Jumlah CR/JR")
        .sort_values("Jumlah CR/JR", ascending=False)
    )
    application_counts["Application Total"] = (
        application_counts.groupby("Aplikasi")["Jumlah CR/JR"]
        .transform("sum")
    )

    return application_counts


def build_seasonality_counts(df, selected_sub):
    if "All" in selected_sub or not selected_sub:
        seasonality_source = df.copy()
    else:
        target_codes = [
            SUB_BIDANG_MAPPING[sub]
            for sub in selected_sub
            if sub in SUB_BIDANG_MAPPING
        ]
        if target_codes:
            seasonality_source = df[
                df["Sub-Bidang"]
                .astype(str)
                .str.contains("|".join(target_codes), na=False)
            ].copy()
        else:
            seasonality_source = df.copy()

    seasonality_source["Tanggal Seasonality"] = pd.to_datetime(
        seasonality_source["Tanggal Masuk"],
        dayfirst=True,
        errors="coerce",
    )
    seasonality_source = seasonality_source[
        seasonality_source["Tanggal Seasonality"].notna()
        & seasonality_source["Jenis"].isin(["CR", "JR"])
    ].copy()

    if seasonality_source.empty:
        return pd.DataFrame()

    seasonality_source["Tahun"] = (
        seasonality_source["Tanggal Seasonality"].dt.year
    )
    seasonality_source["Bulan"] = (
        seasonality_source["Tanggal Seasonality"].dt.month
    )

    min_year = 2024
    max_year = int(
        seasonality_source["Tanggal Seasonality"].dt.year.max()
    )
    seasonality_source = seasonality_source[
        seasonality_source["Tahun"] >= min_year
    ]

    if seasonality_source.empty or max_year < min_year:
        return pd.DataFrame()

    year_range = range(min_year, max_year + 1)

    return (
        seasonality_source.groupby(["Tahun", "Bulan"])
        .size()
        .unstack(fill_value=0)
        .reindex(index=year_range, fill_value=0)
        .reindex(columns=range(1, 13), fill_value=0)
        .sort_index()
    )


# =============================================================================
# AI CONTROLLER
# =============================================================================

def normalize_application_name(value):
    if value is None or pd.isna(value):
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    alias = APPLICATION_ALIASES.get(text)
    if alias:
        return re.sub(r"\s+", " ", alias.lower()).strip()
    return text


def split_application_names(value):
    if value is None or pd.isna(value):
        return []
    text = str(value).strip()
    if not text or text.lower() in {"-", "nan", "none"}:
        return []
    return [
        part.strip()
        for part in re.split(
            r"\s*(?:,|;|\n|\||\+)\s*|\s+/\s+",
            text,
        )
        if part.strip()
    ]


def application_display_name(normalized_name, raw_name):
    canonical_name = APPLICATION_CANONICAL_NAMES.get(normalized_name)
    if canonical_name:
        return canonical_name
    if len(normalized_name) <= 4:
        return normalized_name.upper()
    return normalized_name.title()


@st.cache_data(ttl=600)
def prepare_ai_data(df):
    requests = pd.DataFrame(index=df.index)

    def source(column, default=None):
        if column in df.columns:
            return df[column]
        return pd.Series(default, index=df.index)

    nodin = source("No Nodin Masuk", "").fillna("").astype(str).str.strip()
    requests["request_id"] = [
        f"{index}:{value or 'request'}"
        for index, value in zip(df.index, nodin)
    ]
    requests["judul"] = source("Judul CR", "").fillna("").astype(str).str.strip()
    requests["jenis"] = source("Jenis", "").fillna("").astype(str).str.strip()
    requests["klasifikasi"] = (
        source("Klasifikasi", "").fillna("").astype(str).str.strip()
    )
    requests["request_type"] = (
        requests["jenis"] + " " + requests["klasifikasi"]
    ).str.strip()
    requests["status"] = source("Status", "").fillna("").astype(str).str.strip()
    requests["sub_bidang"] = (
        source("Sub-Bidang", "").fillna("").astype(str).str.strip()
    )
    requests["bpo"] = source("BPO", "").fillna("").astype(str).str.strip()
    requests["pic"] = source("PIC", "").fillna("").astype(str).str.strip()
    requests["progres"] = (
        source("Progres", "").fillna("").astype(str).str.strip()
    )
    requests["keterangan"] = (
        source("Keterangan", "").fillna("").astype(str).str.strip()
    )
    requests["aplikasi_utama"] = (
        source("Nama Aplikasi", "").fillna("").astype(str).str.strip()
    )
    requests["aplikasi_terdampak"] = (
        source("Aplikasi Terdampak", "").fillna("").astype(str).str.strip()
    )
    requests["tanggal_masuk"] = pd.to_datetime(
        source("Tanggal Masuk"),
        dayfirst=True,
        errors="coerce",
    )
    requests["tanggal_laporan"] = pd.to_datetime(
        source("Tanggal Laporan"),
        dayfirst=True,
        errors="coerce",
    )
    requests["tahun_laporan"] = requests["tanggal_laporan"].dt.year.astype(
        "Int64"
    )
    requests["bulan_laporan"] = requests["tanggal_laporan"].dt.month.astype(
        "Int64"
    )
    requests["sla_terpakai"] = source("SLA terpakai")
    requests["sisa_sla"] = pd.to_numeric(
        source("Sisa SLA"),
        errors="coerce",
    )
    requests["target_sla"] = requests["request_type"].map(SLA_TARGETS)
    completed_mask = requests["status"].eq("Selesai")
    requests["pencapaian_sla"] = (
        2
        - (
            requests["sla_terpakai"].where(completed_mask, 0)
            / requests["target_sla"]
        )
    ).mul(100).where(completed_mask)

    active_mask = requests["status"].eq("Aktif")
    requests["sla_alert_status"] = "Not Active"
    requests.loc[
        active_mask & requests["sisa_sla"].lt(0),
        "sla_alert_status",
    ] = "Over SLA"
    requests.loc[
        active_mask & requests["sisa_sla"].between(0, 3),
        "sla_alert_status",
    ] = "Near SLA"
    requests.loc[
        active_mask & requests["sisa_sla"].gt(3),
        "sla_alert_status",
    ] = "Safe"

    application_rows = []
    for request_index, request in requests.iterrows():
        application_sources = [
            ("Nama Aplikasi", request["aplikasi_utama"]),
            ("Aplikasi Terdampak", request["aplikasi_terdampak"]),
        ]
        for matched_from, raw_value in application_sources:
            for application_raw in split_application_names(raw_value):
                normalized = normalize_application_name(application_raw)
                if not normalized:
                    continue
                application_rows.append(
                    {
                        "request_id": request["request_id"],
                        "application_raw": application_raw,
                        "application_normalized": normalized,
                        "application": application_display_name(
                            normalized,
                            application_raw,
                        ),
                        "matched_from": matched_from,
                    }
                )

    applications = pd.DataFrame(
        application_rows,
        columns=[
            "request_id",
            "application_raw",
            "application_normalized",
            "application",
            "matched_from",
        ],
    )
    if not applications.empty:
        applications = applications.drop_duplicates(
            ["request_id", "application_normalized", "matched_from"]
        ).reset_index(drop=True)

    return requests.reset_index(drop=True), applications


def find_matching_applications(applications, query):
    if applications.empty or not query:
        return applications.iloc[0:0], []

    normalized_query = normalize_application_name(query)
    if not normalized_query:
        return applications.iloc[0:0], []

    matched_rows = applications[
        applications["application_normalized"] == normalized_query
    ].copy()
    labels = sorted(matched_rows["application"].dropna().unique().tolist())
    return matched_rows, labels


def filter_ai_requests(
    requests,
    applications,
    arguments,
    default_year,
):
    filtered = requests.copy()
    selected_year = arguments.get("year")
    if selected_year is None:
        selected_year = default_year
    if selected_year is not None:
        filtered = filtered[
            filtered["tahun_laporan"] == int(selected_year)
        ]

    statuses = arguments.get("statuses") or []
    if statuses:
        filtered = filtered[filtered["status"].isin(statuses)]

    jenis = arguments.get("jenis")
    if jenis:
        filtered = filtered[filtered["jenis"] == jenis]

    selected_bpo = arguments.get("bpo")
    if selected_bpo:
        normalized_bpo = str(selected_bpo).strip().casefold()
        filtered = filtered[
            filtered["bpo"].astype(str).str.strip().str.casefold()
            == normalized_bpo
        ]

    request_types = arguments.get("request_types") or []
    if request_types:
        filtered = filtered[
            filtered["request_type"].isin(request_types)
        ]

    sla_alert_statuses = arguments.get("sla_alert_statuses") or []
    if sla_alert_statuses:
        filtered = filtered[
            filtered["sla_alert_status"].isin(sla_alert_statuses)
        ]

    matched_applications = []
    application_query = arguments.get("application")
    if application_query:
        matched_rows, matched_applications = find_matching_applications(
            applications,
            application_query,
        )
        filtered = filtered[
            filtered["request_id"].isin(matched_rows["request_id"])
        ]

    return filtered, int(selected_year), matched_applications


def get_sla_summary(
    requests,
    applications,
    arguments,
    default_year,
):
    summary_arguments = {
        **arguments,
        "statuses": ["Selesai"],
    }
    filtered, selected_year, _ = filter_ai_requests(
        requests,
        applications,
        summary_arguments,
        default_year,
    )
    selected_types = arguments.get("request_types") or KPI_LABELS
    results = []
    for request_type in KPI_LABELS:
        if request_type not in selected_types:
            continue
        category = filtered[filtered["request_type"] == request_type]
        target = SLA_TARGETS[request_type]
        realization = (
            category["sla_terpakai"].mean()
            if not category.empty
            else None
        )
        achievement = (
            (2 - (realization / target)) * 100
            if realization is not None
            else None
        )
        results.append(
            {
                "request_type": request_type,
                "count": int(len(category)),
                "sla_sample_count": int(len(category)),
                "target_sla": target,
                "realisasi_sla": (
                    round(float(realization), 2)
                    if realization is not None
                    else None
                ),
                "pencapaian_percent": (
                    round(float(achievement), 2)
                    if achievement is not None
                    else None
                ),
            }
        )

    return {
        "year": selected_year,
        "status": "Selesai",
        "formula": "(2 - Realisasi SLA / Target SLA) x 100%",
        "categories": results,
    }


def rank_ai_groups(
    requests,
    applications,
    arguments,
    default_year,
):
    metric = arguments["metric"]
    ranking_arguments = dict(arguments)
    if metric != "count":
        ranking_arguments["statuses"] = ["Selesai"]
    filtered, selected_year, matched_applications = filter_ai_requests(
        requests,
        applications,
        ranking_arguments,
        default_year,
    )

    group_by = arguments["group_by"]
    group_columns = {
        "bpo": "bpo",
        "sub_bidang": "sub_bidang",
        "pic": "pic",
        "request_type": "request_type",
    }
    if group_by == "application":
        group_source = applications[
            applications["request_id"].isin(filtered["request_id"])
        ].copy()
        if arguments.get("application"):
            matching_rows, _ = find_matching_applications(
                applications,
                arguments["application"],
            )
            group_source = group_source[
                group_source["application_normalized"].isin(
                    matching_rows["application_normalized"]
                )
            ]
        group_source = group_source.drop_duplicates(
            ["request_id", "application_normalized"]
        ).merge(
            filtered[["request_id", "sla_terpakai"]],
            on="request_id",
            how="left",
        )
        group_column = "application"
    else:
        group_source = filtered.copy()
        group_column = group_columns[group_by]

    group_source[group_column] = (
        group_source[group_column].fillna("").astype(str).str.strip()
    )
    group_source = group_source[
        ~group_source[group_column].isin(["", "-", "nan", "None"])
    ]
    if group_source.empty:
        return {
            "year": selected_year,
            "group_by": group_by,
            "metric": metric,
            "matching_request_count": int(len(filtered)),
            "matched_applications": matched_applications,
            "results": [],
        }

    grouped = group_source.groupby(group_column, dropna=False)
    result = grouped["request_id"].nunique().rename("request_count").to_frame()
    if metric == "count":
        result["sla_sample_count"] = 0
        result["value"] = result["request_count"]
    else:
        grouped = group_source.groupby(group_column, dropna=False)
        result["sla_sample_count"] = grouped["sla_terpakai"].count()
        aggregations = {
            "average_sla": "mean",
            "median_sla": "median",
            "maximum_sla": "max",
        }
        result["value"] = grouped["sla_terpakai"].agg(
            aggregations[metric]
        )

    default_minimum = (
        3
        if group_by == "application" and metric != "count"
        else 1
    )
    minimum_count = int(
        arguments.get("minimum_count", default_minimum)
    )
    qualifying_count = (
        result["request_count"]
        if metric == "count"
        else result["sla_sample_count"]
    )
    result = result[
        (qualifying_count >= minimum_count)
        & result["value"].notna()
    ]
    ascending = arguments.get("sort_order", "descending") == "ascending"
    result = result.sort_values(
        ["value", "request_count"],
        ascending=[ascending, ascending],
    ).head(int(arguments.get("limit", 5)))

    return {
        "year": selected_year,
        "group_by": group_by,
        "metric": metric,
        "statuses": ranking_arguments.get("statuses") or [],
        "matching_request_count": int(len(filtered)),
        "minimum_count": minimum_count,
        "matched_applications": matched_applications,
        "results": [
            {
                "label": str(label),
                "request_count": int(row["request_count"]),
                "sla_sample_count": int(row["sla_sample_count"]),
                "value": round(float(row["value"]), 2),
            }
            for label, row in result.iterrows()
        ],
    }


def find_ai_requests(
    requests,
    applications,
    arguments,
    default_year,
):
    search_arguments = dict(arguments)
    sort_by = arguments.get("sort_by")
    if not sort_by:
        sort_by = (
            "sisa_sla"
            if arguments.get("sla_alert_statuses")
            else "sla_terpakai"
        )
    if (
        search_arguments.get("sla_alert_statuses")
        and not search_arguments.get("statuses")
    ):
        search_arguments["statuses"] = ["Aktif"]
    elif sort_by == "sla_terpakai":
        search_arguments["statuses"] = ["Selesai"]
    filtered, selected_year, matched_applications = filter_ai_requests(
        requests,
        applications,
        search_arguments,
        default_year,
    )

    sort_order = arguments.get("sort_order")
    if not sort_order:
        sort_order = (
            "ascending"
            if sort_by in {"sisa_sla", "tanggal_masuk"}
            else "descending"
        )
    filtered = filtered.sort_values(
        sort_by,
        ascending=sort_order == "ascending",
        na_position="last",
    )
    max_rows = int(arguments.get("max_rows", 5))

    application_lookup = (
        applications.groupby("request_id")
        .agg(
            applications=(
                "application",
                lambda values: sorted(set(values)),
            ),
            application_sources=(
                "matched_from",
                lambda values: sorted(set(values)),
            ),
        )
        .to_dict("index")
        if not applications.empty
        else {}
    )

    records = []
    for _, row in filtered.head(max_rows).iterrows():
        application_data = application_lookup.get(row["request_id"], {})
        sla_terpakai = row["sla_terpakai"]
        records.append(
            {
                "judul": row["judul"] or None,
                "request_type": row["request_type"] or None,
                "status": row["status"] or None,
                "applications": application_data.get("applications", []),
                "application_sources": application_data.get(
                    "application_sources",
                    [],
                ),
                "sla_terpakai": (
                    round(float(sla_terpakai), 2)
                    if pd.notna(sla_terpakai)
                    and str(sla_terpakai).strip() not in {"", "-"}
                    else None
                ),
                "sisa_sla": (
                    round(float(row["sisa_sla"]), 2)
                    if pd.notna(row["sisa_sla"])
                    else None
                ),
                "target_sla": (
                    round(float(row["target_sla"]), 2)
                    if pd.notna(row["target_sla"])
                    else None
                ),
                "sla_alert_status": row["sla_alert_status"],
                "progres": row["progres"] or None,
                "keterangan": row["keterangan"] or None,
                "pic": row["pic"] or None,
                "sub_bidang": row["sub_bidang"] or None,
                "bpo": row["bpo"] or None,
                "tanggal_masuk": (
                    row["tanggal_masuk"].strftime("%Y-%m-%d")
                    if pd.notna(row["tanggal_masuk"])
                    else None
                ),
            }
        )

    return {
        "year": selected_year,
        "filters": {
            key: value
            for key, value in search_arguments.items()
            if value not in (None, [], "")
        },
        "matched_applications": matched_applications,
        "matching_count": int(len(filtered)),
        "returned_count": len(records),
        "truncated": len(filtered) > len(records),
        "records": records,
    }


AI_INSTRUCTIONS = """
Kamu adalah Joni, asisten analitik dashboard CR/JR ITBP.

Context:
- Kamu memiliki persona anak gaul yang knowledgable
- Jawab dalam Bahasa Indonesia kasual yang ringkas.
- Gunakan kata "Saya" untuk menyebut diri sendiri dan "Bos" untuk lawan bicara
- Kamu memiliki akses terhadap beberapa database histori Change Request (CR) dan Job Request (JR) milik Divisi Manajemen Digital PT PLN
- Pelaksanaan CR JR oleh ITBP memiliki KPI berupa target SLA pada setiap jenis CR dan JR
- Target SLA resmi: CR Mayor 12.72, CR Minor 5.28, CR Darurat 1.0,
  JR Mayor 13.0, dan JR Minor 13.0.
- SLA terpakai adalah waktu pemrosesan request yang sudah selesai.
- Sisa SLA digunakan untuk request aktif. Over SLA berarti Sisa SLA < 0 dan
  Near SLA berarti 0 <= Sisa SLA <= 3.
- Kata "kritis" bisa berarti dua hal. Jika pengguna menyebut SLA, over,
  deadline, terlambat, atau hampir lewat, artikan sebagai SLA alert. Jika
  pengguna menyebut penting, prioritas, impact bisnis, atau business critical,
  jangan gunakan SLA alert kecuali diminta. Jika hanya bertanya "kritis"
  tanpa konteks, tanyakan klarifikasi singkat.

Rules:
- Untuk pertanyaan tentang data dashboard, selalu gunakan tool dan jangan
  menghitung atau menebak sendiri.
- Jika pengguna tidak menyebut tahun, biarkan tool menggunakan tahun 2026
- Untuk request paling lama diproses, gunakan Status Selesai dan urutkan
  SLA terpakai dari terbesar.
- Saat menjawab request near SLA atau over SLA, sebutkan Judul CR, Sisa SLA,
  Progres, Keterangan, PIC, dan aplikasi bila tersedia.
- Untuk ranking berdasarkan SLA, sebutkan jumlah request yang menjadi sampel.
- Jika hasil tool kosong, maka kamu bisa interpretasi sendiri.

Output Style:
- Default jawaban adalah ringkasan, bukan dump hasil tool.
- Jangan tampilkan JSON mentah, nama field teknis, atau seluruh records kecuali diminta.
- Untuk ranking, tampilkan maksimal 5 poin utama secara default.
- Untuk daftar request, tampilkan maksimal 3 request paling relevan secara default,
  lalu ringkas pola besarnya.
- Jika tool mengembalikan lebih banyak data daripada yang kamu tampilkan, sebutkan
  jumlah total data yang cocok secara singkat.
- Berikan rincian lebih lengkap hanya jika pengguna meminta dengan kata seperti:
  detail, rinci, lengkap, semua, list, daftar, tabel, atau "show me".
- Jika pengguna meminta detail, boleh tampilkan lebih banyak item sesuai hasil tool.
- Jangan hanya menyalin output tool. Interpretasikan hasilnya dengan menyampaikan
  temuan utama, pola, perbandingan, dan implikasi yang relevan terhadap pertanyaan.

Important:
- Jika pertanyaan tidak berkaitan dengan dashboard CR/JR, jawab singkat bahwa
  kamu hanya melayani analisis dashboard CR/JR ITBP.
"""


def execute_ai_tool(
    tool_name,
    arguments,
    requests,
    applications,
    selected_year,
):
    handlers = {
        "get_sla_summary": lambda: get_sla_summary(
            requests,
            applications,
            arguments,
            selected_year,
        ),
        "rank_groups": lambda: rank_ai_groups(
            requests,
            applications,
            arguments,
            selected_year,
        ),
        "find_requests": lambda: find_ai_requests(
            requests,
            applications,
            arguments,
            selected_year,
        ),
    }
    handler = handlers.get(tool_name)
    if not handler:
        return {"error": f"Tool tidak dikenal: {tool_name}"}
    try:
        return handler()
    except Exception as error:
        return {"error": str(error), "tool": tool_name}


def execute_ai_assistant(prompt, client, df, selected_year):
    if not client:
        return (
            "OpenAI client is not initialized. "
            "Please configure your API key."
        )
    if df.empty:
        return "Data dashboard belum tersedia."

    history = st.session_state.chat_history
    if (
        history
        and history[-1]["role"] == "user"
        and history[-1]["content"] == prompt
    ):
        history = history[:-1]

    api_input = [
        {"role": message["role"], "content": message["content"]}
        for message in history[-4:]
    ]
    api_input.append({"role": "user", "content": prompt})
    requests, applications = prepare_ai_data(df)

    try:
        response = client.responses.create(
            model="gpt-5.4-nano",
            instructions=AI_INSTRUCTIONS,
            input=api_input,
            tools=ACTIVE_AI_TOOLS,
            tool_choice="auto",
            parallel_tool_calls=True,
        )

        for _ in range(3):
            tool_calls = [
                item
                for item in response.output
                if item.type == "function_call"
            ]
            if not tool_calls:
                return response.output_text or "Tidak ada respons."

            tool_outputs = []
            for tool_call in tool_calls:
                arguments = json.loads(tool_call.arguments)
                result = execute_ai_tool(
                    tool_call.name,
                    arguments,
                    requests,
                    applications,
                    selected_year,
                )
                tool_outputs.append(
                    {
                        "type": "function_call_output",
                        "call_id": tool_call.call_id,
                        "output": json.dumps(
                            result,
                            ensure_ascii=False,
                            default=str,
                        ),
                    }
                )

            response = client.responses.create(
                model="gpt-5.4-nano",
                instructions=AI_INSTRUCTIONS,
                previous_response_id=response.id,
                input=tool_outputs,
                tools=ACTIVE_AI_TOOLS,
                tool_choice="auto",
                parallel_tool_calls=True,
            )

        return "Permintaan membutuhkan terlalu banyak langkah tool."
    except Exception as error:
        return f"Gagal memproses perintah: {str(error)}"


# =============================================================================
# PRESENTATION HELPERS
# =============================================================================

def highlight_urgent_sla(row):
    try:
        sla_val = int(row["Sisa SLA"])
    except (ValueError, TypeError):
        sla_val = None

    default_style = [""] * len(row)
    if sla_val is None or sla_val > 3:
        return default_style

    sla_col_idx = row.index.get_loc("Sisa SLA")
    if sla_val <= 1:
        default_style[sla_col_idx] = (
            "background-color: #7f1d1d; color: white; "
            "font-weight: bold; text-align: center;"
        )
    elif sla_val <= 3:
        default_style[sla_col_idx] = (
            "background-color: #FFD300; color: #000000; "
            "font-weight: bold; text-align: center;"
        )

    return default_style


def render_kpi_cards(metrics_output):
    cols = st.columns(5, border=True)

    for index, label in enumerate(KPI_LABELS):
        metric_data = metrics_output.get(label)
        actual_value = metric_data[0] if metric_data else None
        count_value = metric_data[1] if metric_data else None
        target_value = SLA_TARGETS.get(label)

        if actual_value is not None and not pd.isna(actual_value):
            formatted_value = f"{actual_value:,.2f}"
            kpi_ratio = 2 - (actual_value / target_value)
            kpi_percentage = kpi_ratio * 100
            delta_value = f"{kpi_percentage:.2f}%"
            delta_color = (
                "normal" if kpi_percentage >= 100 else "inverse"
            )
            delta_arrow = "off"
        else:
            formatted_value = "N/A"
            delta_value = "No data"
            delta_color = "off"
            delta_arrow = "off"

        with cols[index]:
            st.metric(
                label=label,
                value=formatted_value,
                delta=delta_value,
                delta_color=delta_color,
                delta_arrow=delta_arrow,
            )
            st.caption(f"Jumlah: {count_value}")


def create_itbp_request_chart(request_counts, team_summary):
    chart_data = request_counts.copy()
    chart_data["Tim"] = "ITBP " + chart_data["Sub-Bidang"]
    team_order = [f"ITBP {code}" for code in SUB_BIDANG_CODES]

    fig = px.bar(
        chart_data,
        x="Tim",
        y="Jumlah CR/JR",
        color="Jenis",
        text="Jumlah CR/JR",
        category_orders={
            "Tim": team_order,
            "Jenis": ["CR", "JR"],
        },
        color_discrete_map={
            "CR": "#0F766E",
            "JR": "#F59E0B",
        },
        labels={
            "Tim": "Sub-Bidang",
            "Jumlah CR/JR": "Number of CR/JR",
            "Jenis": "Jenis",
        },
    )
    fig.update_layout(
        barmode="stack",
        height=420,
        margin=dict(l=20, r=20, t=50, b=20),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
        ),
    )
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(
        showgrid=True,
        gridcolor="#1E293B",
        zeroline=False,
    )
    fig.update_traces(
        textposition="inside",
        textfont=dict(color="#FFFFFF"),
        marker=dict(line=dict(width=0)),
    )

    for _, row in team_summary.iterrows():
        fig.add_annotation(
            x=f"ITBP {row['Sub-Bidang']}",
            y=row["Total_Request"],
            text=(
                f"{int(row['Total_Request'])} requests"
                f"<br>{row['Request_per_Person']:.2f}/person"
            ),
            showarrow=False,
            yshift=24,
            font=dict(size=11, color="#FFFFFF"),
        )

    return fig


def create_itbp_sla_chart(
    sla_records,
    team_summary,
    selected_request_type,
):
    chart_data = sla_records.dropna(subset=["SLA terpakai"]).copy()
    team_positions = {
        code: index for index, code in enumerate(SUB_BIDANG_CODES)
    }
    chart_data["Tim"] = "ITBP " + chart_data["Sub-Bidang"]
    chart_data["X Position"] = chart_data["Sub-Bidang"].map(team_positions)
    chart_data["Jitter"] = (
        chart_data.groupby("Sub-Bidang").cumcount()
        - chart_data.groupby("Sub-Bidang")["Sub-Bidang"].transform(
            "size"
        ).sub(1).div(2)
    )
    max_distance = chart_data.groupby("Sub-Bidang")["Jitter"].transform(
        lambda values: max(values.abs().max(), 1)
    )
    chart_data["X Position"] += chart_data["Jitter"] / max_distance * 0.22

    color_map = {
        "CR Mayor": "#38BDF8",
        "CR Minor": "#22C55E",
        "CR Darurat": "#FF0000",
        "JR Mayor": "#FFAE56",
        "JR Minor": "#FACC15",
    }
    fig = go.Figure()
    visible_types = (
        KPI_LABELS
        if selected_request_type == "All"
        else [selected_request_type]
    )
    for request_type in visible_types:
        type_data = chart_data[
            chart_data["Request Type"] == request_type
        ]
        if type_data.empty:
            continue
        fig.add_trace(
            go.Scatter(
                x=type_data["X Position"],
                y=type_data["SLA terpakai"],
                mode="markers",
                name=request_type,
                customdata=type_data[
                    ["Tim", "Request Type", "Judul CR"]
                ],
                marker=dict(
                    size=8,
                    color=color_map[request_type],
                    opacity=1,
                ),
                hovertemplate=(
                    "Sub-Bidang: %{customdata[0]}<br>"
                    "Request Type: %{customdata[1]}<br>"
                    "%{customdata[2]}<br>"
                    "SLA terpakai: %{y:.2f}<extra></extra>"
                ),
            )
        )

    averages = team_summary.dropna(subset=["Rata_Rata_SLA"]).copy()
    averages["X Position"] = averages["Sub-Bidang"].map(team_positions)
    fig.add_trace(
        go.Scatter(
            x=averages["X Position"],
            y=averages["Rata_Rata_SLA"],
            mode="markers+text",
            name="Average",
            text=averages["Rata_Rata_SLA"].map(lambda value: f"{value:.2f}"),
            textposition="top center",
            marker=dict(
                size=13,
                # symbol="diamond",
                color="#FFFFFF",
                opacity=0.9,
                line=dict(color="#FFDA62", width=2, dash="dot"),
            ),
            customdata=averages[["Sub-Bidang"]],
            hovertemplate=(
                "Sub-Bidang: ITBP %{customdata[0]}<br>"
                "Average SLA: %{y:.2f}<extra></extra>"
            ),
        )
    )

    if selected_request_type != "All":
        target = SLA_TARGETS[selected_request_type]
        fig.add_hline(
            y=target,
            line_dash="dash",
            line_color="#EF4444",
            annotation_text=(
                f"Target SLA {selected_request_type}: {target:.2f}"
            ),
            annotation_position="top left",
        )

    fig.update_layout(
        height=420,
        margin=dict(l=20, r=20, t=50, b=20),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(
            title="Sub-Bidang",
            tickmode="array",
            tickvals=list(team_positions.values()),
            ticktext=[f"ITBP {code}" for code in SUB_BIDANG_CODES],
            range=[-0.5, len(SUB_BIDANG_CODES) - 0.5],
        ),
        yaxis_title="SLA terpakai",
        legend_title_text="Request Type",
    )
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(
        showgrid=True,
        gridcolor="#1E293B",
        zeroline=True,
        zerolinecolor="#94A3B8",
    )

    return fig


def create_bpo_chart(stacked_counts, total_counts, bpo_names):
    fig = px.bar(
        stacked_counts,
        x="Jumlah CR/JR",
        y="BPO",
        color="Status",
        orientation="h",
        text="Jumlah CR/JR",
        category_orders={"BPO": bpo_names[::1]},
        color_discrete_map=ANALYTICS_COLOR_MAP,
        labels={
            "Jumlah CR/JR": "Count",
            "BPO": "BPO Division",
            "Status": "Status",
        },
    )

    fig.update_layout(
        barmode="stack",
        xaxis_title="Number of CR/JR",
        yaxis_title="BPO Division",
        margin=dict(l=20, r=80, t=20, b=20),
        height=480,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
        ),
        yaxis=dict(
            range=(
                [len(bpo_names) - 10.5, len(bpo_names) - 0.5]
                if len(bpo_names) > 10
                else None
            ),
            fixedrange=False,
        ),
        dragmode="pan",
        clickmode="event+select",
    )

    fig.update_yaxes(showgrid=False)
    fig.update_xaxes(
        showgrid=True,
        gridcolor="#1E293B",
        zeroline=False,
    )
    fig.update_traces(
        textposition="inside",
        textfont=dict(size=11, color="#FFFFFF"),
        marker=dict(line=dict(width=0)),
    )

    for _, row in total_counts.iterrows():
        fig.add_annotation(
            x=row["Total"],
            y=row["BPO"],
            text=f"  Total: {row['Total']}",
            showarrow=False,
            xanchor="left",
            yanchor="middle",
            font=dict(size=12, color="#FFFFFF", weight="bold"),
        )

    return fig


def create_application_chart(application_counts):
    fig = px.treemap(
        application_counts,
        path=["Aplikasi", "Request Type"],
        values="Jumlah CR/JR",
        color="Application Total",
        color_continuous_scale='Tealgrn',
        labels={
            "Jumlah CR/JR": "Count",
            "Aplikasi": "Application",
            "Request Type": "Request Type",
            "Application Total": "Application Total",
        },
    )
    fig.update_layout(
        margin=dict(l=10, r=10, t=10, b=10),
        height=480,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        coloraxis_showscale=False
    )
    fig.update_traces(
        texttemplate="<b>%{label}</b><br>%{value} request",
        textfont=dict(size=13),
        hovertemplate=(
            "%{label}<br>"
            "Jumlah request: %{value}<br>"
            "Total aplikasi: %{color:.0f}<extra></extra>"
        ),
        marker=dict(line=dict(color="#111827", width=1)),
    )
    return fig


def create_seasonality_heatmap(seasonality_counts):
    month_labels = [
        "Jan",
        "Feb",
        "Mar",
        "Apr",
        "Mei",
        "Jun",
        "Jul",
        "Agu",
        "Sep",
        "Okt",
        "Nov",
        "Des",
    ]
    years = seasonality_counts.index.astype(int).tolist()
    values = seasonality_counts.to_numpy()
    max_value = int(values.max()) if values.size else 0

    fig = go.Figure(
        data=go.Heatmap(
            z=values,
            x=month_labels,
            y=years,
            colorscale="YlGnBu",
            zmin=0,
            zmax=max(max_value, 1),
            xgap=2,
            ygap=2,
            colorbar=dict(title="Jumlah"),
            hovertemplate=(
                "Tahun: %{y}<br>"
                "Bulan: %{x}<br>"
                "Jumlah CR/JR: %{z}<extra></extra>"
            ),
            showscale=False,
            # height=350,
            # margin=dict(l=60, r=80, t=30, b=60)
        )
        
    )
    # fig.update_layout(
    #     height=350,
    #     margin=dict(l=60, r=80, t=30, b=60)
    # )

    fig.update_yaxes(
        domain=[0.25, 0.75]
    )

    for row_index, year in enumerate(years):
        for column_index, month in enumerate(month_labels):
            value = int(values[row_index, column_index])
            text_color = (
                "#FFFFFF"
                if max_value and value >= max_value * 0.55
                else "#111827"
            )
            fig.add_annotation(
                x=month,
                y=year,
                text=str(value),
                showarrow=False,
                font=dict(size=12, color=text_color),
            )

    fig.update_layout(
        xaxis_title="Bulan",
        yaxis_title="Tahun",
        margin=dict(l=20, r=20, t=20, b=20),
        height=max(320, 70 * len(years) + 140),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    fig.update_xaxes(side="bottom", fixedrange=True)
    fig.update_yaxes(
        tickmode="array",
        tickvals=years,
        ticktext=[str(year) for year in years],
        range=[min(years) - 0.5, max(years) + 0.5],
        fixedrange=True,
    )

    return fig


# =============================================================================
# PAGE SECTIONS
# =============================================================================

def render_sidebar(client, df):
    with st.sidebar:
        st.title("Dashboard Controls")
        selected_year = st.selectbox(
            "Pilih Tahun",
            options=[2024, 2025, 2026, 2027],
            key="dashboard_year",
        )

        st.markdown("---")
        st.subheader("🤖 Joni - Asisten AI")

        for message in st.session_state.chat_history:
            with st.chat_message(message["role"]):
                st.write(message["content"])

        user_input = st.chat_input(
            "Berikan perintah untuk mencari data atau tampilan dashboard..."
        )
        if user_input:
            st.session_state.chat_history.append(
                {"role": "user", "content": user_input}
            )
            with st.chat_message("user"):
                st.write(user_input)

            with st.chat_message("assistant"):
                with st.spinner("Bentar..."):
                    ai_response = execute_ai_assistant(
                        user_input,
                        client,
                        df,
                        selected_year,
                    )
                    st.write(ai_response)

            st.session_state.chat_history.append(
                {"role": "assistant", "content": ai_response}
            )
            st.rerun()

    return selected_year


def render_active_summary(active_df):
    layout_left, layout_right = st.columns([5, 5])

    with layout_left:
        st.subheader("Monitoring CR/JR Aktif")
        st.caption(f"Total CR/JR aktif: **{len(active_df)}**")

        urgent_count = calculate_urgent_count(active_df)
        exurgent_count = calculate_exurgent_count(active_df)
        if urgent_count > 0:
            if exurgent_count > 0:
                st.markdown(
                    f"""
                    <div style="background-color: #7F1D1D; border: 0px solid #7F1D1D; border-radius: 6px; padding: 5px 14px; color: #FFFFFF; font-size: 12px; font-weight: 500; margin-top: 0px; margin-bottom: 4px;">
                        🚨 <strong>{urgent_count} CR/JR</strong> mendekati batas SLA | <strong>{exurgent_count} CR/JR</strong> sudah melewati batas SLA!
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    f"""
                    <div style="background-color: #2D1A1A; border: 0px solid #7F1D1D; border-radius: 6px; padding: 5px 14px; color: #FF8080; font-size: 12px; font-weight: 500; margin-top: 0px; margin-bottom: 4px;">
                        🔴 <strong>{urgent_count} CR/JR</strong> mendekati batas SLA (≤ 3 hari)!
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

        if st.session_state.filter_sub_bidang != "All":
            if st.button("❌ Clear Filter", type="primary"):
                st.session_state.filter_sub_bidang = "All"
                st.rerun()

    with layout_right:
        card_cols = st.columns(4)

        for index, sub_bidang in enumerate(SUB_BIDANG_CODES):
            sub_count = (
                active_df["Sub-Bidang"]
                .astype(str)
                .str.contains(sub_bidang, na=False)
                .sum()
            )

            with card_cols[index]:
                is_selected = (
                    st.session_state.filter_sub_bidang == sub_bidang
                )
                bg_color = "#1E293B" if is_selected else "transparent"
                border_color = (
                    "#38BDF8" if is_selected else "transparent"
                )
                text_color = "#38BDF8" if is_selected else "inherit"
                label = SUB_BIDANG_LABELS[sub_bidang]

                st.markdown(
                    f"""
                    <div style="background-color: {bg_color}; border: 1px solid {border_color}; border-radius: 8px; padding: 6px 10px; text-align: center; margin-bottom: 8px;">
                        <div style="color: {text_color}; font-size: 13px; font-weight: 500; line-height: 1.2; margin-bottom: 2px; min-height: 38px;">{label}</div>
                        <div style="color: {text_color}; font-size: 26px; font-weight: bold; line-height: 1; margin: 0;">{sub_count}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

                button_label = "" if is_selected else "Pilih"
                if st.button(
                    button_label,
                    key=f"btn_filter_{sub_bidang}",
                    use_container_width=True,
                ):
                    st.session_state.filter_sub_bidang = sub_bidang
                    st.rerun()


def render_active_table(active_df):
    if st.session_state.filter_sub_bidang != "All":
        selected_sub_bidang = st.session_state.filter_sub_bidang
        st.write(
            "🔎 *Filtering table for:* "
            f"**ITBP {selected_sub_bidang}**"
        )
        displayed_df = active_df[
            active_df["Sub-Bidang"]
            .astype(str)
            .str.contains(selected_sub_bidang, na=False)
        ].copy()
    else:
        displayed_df = active_df.copy()

    displayed_df = (
        displayed_df.sort_values(by="Sisa SLA", ascending=True)
        .reset_index(drop=True)
    )
    displayed_df.index = displayed_df.index + 1

    view_columns = [
        "Sisa SLA",
        "Judul CR",
        "Aplikasi",
        "Sub-Bidang",
        "Kategori",
        "BPO",
        "PIC",
        "Lingkup Perubahan",
        "Progres",
    ]
    display_columns = [
        column
        for column in view_columns
        if column in displayed_df.columns
    ]

    styled_df = displayed_df[display_columns].style.apply(
        highlight_urgent_sla,
        axis=1,
    )
    st.dataframe(styled_df, use_container_width=True)


def render_active_tab(active_df):
    if active_df.empty:
        st.info("Tidak ada data aktif yang tersedia.")
        return

    render_active_summary(active_df)
    render_active_table(active_df)


def render_itbp_analytics(analytics_df, selected_year):
    st.subheader("ITBP Analytics")
    st.caption(
        f"Section ini menunjukkan bagaimana distribusi dan performa pengerjaan request di ITBP berdasarkan Sub-Bidang selama tahun {selected_year}.\n"
        "Pada chart kiri dapat dilihat jumlah request yang telah diselesaikan per sub-bidang beserta jenisnya.\n"
        "Chart sebelah kanan menunjukkan distribusi waktu pengerjaan setiap request pada tiap sub-bidang.\n"
        "Bagian ini dapat membantu untuk melihat distribusi beban CR/JR, evaluasi bottleneck, dan identifikasi area untuk perbaikan proses."
    )
    st.text(f"Total Request {selected_year}: {len(analytics_df[analytics_df['Status'] == 'Selesai'])}")

    filter_column, _, _, _ = st.columns(4)
    with filter_column:
        selected_request_type = st.selectbox(
            "Filter by Request Type:",
            options=["All", *KPI_LABELS],
            key="itbp_request_type_filter",
        )

    request_counts, team_summary, sla_records = build_itbp_analytics_data(
        analytics_df,
        selected_request_type,
    )
    if request_counts.empty:
        st.warning(
            "Tidak ada data ITBP Analytics untuk filter yang dipilih."
        )
        return

    request_column, sla_column = st.columns(2)
    with request_column:
        st.markdown("#### Number of CR/JR by Sub-Bidang")
        request_fig = create_itbp_request_chart(
        request_counts,
        team_summary,
        )
        # Define custom config to restrict interactivity
        chart_config = {
            # Explicitly remove all buttons EXCEPT the screenshot (toImage) button
            'modeBarButtonsToRemove': [
                'zoom2d', 'pan2d', 'select2d', 'lasso2d', 'zoomIn2d', 
                'zoomOut2d', 'autoScale2d', 'resetScale2d', 'hoverClosestCartesian', 
                'hoverCompareCartesian', 'toggleSpikelines'
            ],
            # Prevents users from zooming in by scrolling
            'scrollZoom': False,
        }
        #Prevent drag-to-zoom behavior on the chart itself
        request_fig.update_layout(dragmode=False)

        st.plotly_chart(
            request_fig,
            use_container_width=True,
            config=chart_config,
        )

    with sla_column:
        st.markdown("#### Average SLA by Sub-Bidang")
        sla_fig = create_itbp_sla_chart(
            sla_records,
            team_summary,
            selected_request_type,
        )
        # Define custom config to restrict interactivity
        chart_config = {
            # Explicitly remove 
            'modeBarButtonsToRemove': [
                'zoom2d', 'pan2d', 'select2d', 'lasso2d', 'zoomIn2d', 
                'zoomOut2d', 'autoScale2d', 'hoverClosestCartesian', 
                'hoverCompareCartesian', 'toggleSpikelines'
            ],
            # Prevents users from zooming in by scrolling
            'scrollZoom': False,
        }

        st.plotly_chart(
            sla_fig,
            use_container_width=True,   
            config=chart_config,
        )

    st.markdown("---")


def render_analytics_tab(analytics_df, seasonality_df, selected_year):
    render_itbp_analytics(analytics_df, selected_year)

    st.subheader("BPO Analytics")
    st.caption("Analisis distribusi CR/JR berdasarkan BPO dan aplikasi mana yang paling sering di CRkan. Gunakan filter by Status dan Sub-Bidang untuk melihat distribusi yang lebih spesifik."
                "Coba klik salah satu bar BPO untuk melihat breakdown aplikasinya.")
    st.caption("Data yang ditampilkan merupakan CR/JR yang sudah selesai / approved oleh MDG.")

    filter_col1, filter_col2,_,_ = st.columns(4)
    with filter_col1:
        selected_statuses = st.multiselect(
            label="Filter by Status:",
            options=STATUS_OPTIONS,
            key="analytics_status_filter",
        )
    with filter_col2:
        selected_sub = st.multiselect(
            label="Filter by Sub-Bidang:",
            options=SUB_BIDANG_OPTIONS,
            key="analytics_sub_filter",
        )

    if analytics_df.empty:
        st.warning(
            "Tidak ada data BPO Analytics untuk tahun yang dipilih."
        )
    else:
        filtered_df = filter_analytics_data(
            analytics_df,
            selected_statuses,
            selected_sub,
        )

        if filtered_df.empty:
            st.warning(
                "Tidak ada data yang sesuai dengan filter BPO Analytics."
            )
        else:
            stacked_counts, total_counts, bpo_names = (
                build_analytics_counts(filtered_df)
            )
            bpo_fig = create_bpo_chart(
                stacked_counts,
                total_counts,
                bpo_names,
            )
            selected_bpo = st.session_state.analytics_selected_bpo
            if selected_bpo not in bpo_names:
                selected_bpo = None
                st.session_state.analytics_selected_bpo = None

            bpo_column, application_column = st.columns(2)
            with bpo_column:
                bpo_event = st.plotly_chart(
                    bpo_fig,
                    use_container_width=True,
                    key=(
                        "bpo_analytics_selector_"
                        f"{st.session_state.bpo_chart_version}"
                    ),
                    on_select="rerun",
                    selection_mode="points",
                    config={"dragmode": "pan",
                        "modeBarButtonsToRemove": ["zoom2d", "zoomIn2d", "zoomOut2d","lasso2d","select2d"],}, 
                )
                

            selected_points = bpo_event.selection.points
            if selected_points:
                clicked_bpo = selected_points[0].get("y")
                if clicked_bpo in bpo_names:
                    selected_bpo = clicked_bpo
                    st.session_state.analytics_selected_bpo = clicked_bpo

            with application_column:
                header_column, reset_column = st.columns([4, 1])
                with reset_column:
                    if selected_bpo and st.button(
                        "Reset",
                        key="clear_bpo_selection",
                        use_container_width=True,
                    ):
                        st.session_state.analytics_selected_bpo = None
                        st.session_state.bpo_chart_version += 1
                        st.rerun()

                application_counts = build_application_counts(
                    filtered_df,
                    selected_bpo,
                )

                if application_counts.empty:
                    st.warning(
                        "Tidak ada data aplikasi untuk filter yang dipilih."
                    )
                else:
                    application_fig = create_application_chart(
                        application_counts,
                    )
                    st.plotly_chart(
                        application_fig,
                        use_container_width=True,
                        config={"displayModeBar": False},
                    )

    st.subheader("Seasonality Analytics")
    st.caption(
        "Chart ini menunjukkan tren masuknya CR/JR per bulan pada setiap tahun. Ini membantu mengidentifikasi pola musiman atau periode dengan volume CR/JR yang lebih tinggi, yang dapat berguna untuk perencanaan sumber daya dan manajemen beban kerja."
    )
    st.caption(
        "Data yang ditampilkan merupakan request dengan semua status, termasuk CR/JR yang belum selesai atau batal. Dihitung berdasarkan tanggal masuk Nodin CR/JR."
    )
    seasonality_sub = st.multiselect(
        label="Filter Seasonality by Sub-Bidang:",
        options=SUB_BIDANG_OPTIONS,
        key="seasonality_sub_filter",
    )
    seasonality_counts = build_seasonality_counts(
        seasonality_df,
        seasonality_sub,
    )

    if seasonality_counts.empty:
        st.warning(
            "Tidak ada data seasonality untuk Sub-Bidang yang dipilih."
        )
        return

    seasonality_fig = create_seasonality_heatmap(seasonality_counts)
    
    with st.container(border=True):
        st.plotly_chart(
            seasonality_fig,
            use_container_width=True,
            config={"displayModeBar": False},
        )


def build_report_data(df, selected_year, selected_month):
    report_source = df[df["Status"] == "Selesai"].copy()
    completion_date = pd.to_datetime(
        report_source["Tanggal Laporan"],
        errors="coerce",
    )
    report_source = report_source[
        (completion_date.dt.year == selected_year)
        & (completion_date.dt.month <= selected_month)
    ].copy()
    completion_date = completion_date.loc[report_source.index]
    report_source["Bulan"] = completion_date.dt.month.map(
        INDONESIAN_MONTHS
    )

    bulan_order_list = [
    "Januari", "Februari", "Maret", "April", "Mei", "Juni", 
    "Juli", "Agustus", "September", "Oktober", "November", "Desember"
]

    category_tables = {}
    for category in KPI_LABELS:
        jenis, klasifikasi = category.split(" ", 1)
        category_df = report_source[
            (report_source["Jenis"] == jenis)
            & (report_source["Klasifikasi"] == klasifikasi)
        ].copy()
        for column in REPORT_COLUMNS:
            if column not in category_df.columns:
                category_df[column] = "-"
        category_df = category_df[REPORT_COLUMNS].copy()
        category_df = category_df.replace(r"^\s*$", pd.NA, regex=True)
        category_df["Bulan"] = pd.Categorical(
            category_df["Bulan"], 
            categories=bulan_order_list, 
            ordered=True
        )

        category_df = category_df.sort_values(by="Bulan")
        category_tables[category] = category_df.fillna("-")

    recap_rows = []
    for category in KPI_LABELS:
        jenis, klasifikasi = category.split(" ", 1)
        category_source = report_source[
            (report_source["Jenis"] == jenis)
            & (report_source["Klasifikasi"] == klasifikasi)
        ]
        sla_values = pd.to_numeric(
            category_source["SLA terpakai"],
            errors="coerce",
        ).dropna()
        target_sla = SLA_TARGETS[category]
        realization_sla = sla_values.mean() if not sla_values.empty else None
        achievement = (
            (2 - (realization_sla / target_sla)) * 100
            if realization_sla is not None
            else None
        )
        recap_rows.append(
            {
                "Jenis": (
                    "Change Request" if jenis == "CR" else "Job Request"
                ),
                "Klasifikasi": klasifikasi,
                "Jumlah": len(category_tables[category]),
                "Target SLA": target_sla,
                "Realisasi SLA": (
                    round(realization_sla, 2)
                    if realization_sla is not None
                    else "-"
                ),
                "Pencapaian": (
                    f"{achievement:.2f}%"
                    if achievement is not None
                    else "-"
                ),
            }
        )

    return category_tables, pd.DataFrame(recap_rows)


def create_report_workbook(category_tables, recap_df):
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for category, category_df in category_tables.items():
            sheet_name = category[:31]
            if category_df.empty:
                pd.DataFrame(
                    {"Keterangan": ["Tidak ada data pada kategori ini"]}
                ).to_excel(writer, sheet_name=sheet_name, index=False)
            else:
                category_df.to_excel(
                    writer,
                    sheet_name=sheet_name,
                    index=False,
                )
        recap_df.to_excel(writer, sheet_name="Rekap SLA", index=False)
    output.seek(0)
    return output.getvalue()


def render_report_tab(df):
    st.subheader("Report")
    st.caption(
        "Laporan request selesai dari Januari sampai bulan yang dipilih."
    )

    now = pd.Timestamp.now(tz="Asia/Jakarta")
    completion_dates = pd.to_datetime(
        df.loc[df["Status"] == "Selesai", "Tanggal Laporan"],
        errors="coerce",
    ).dropna()
    available_years = sorted(
        completion_dates.dt.year.astype(int).unique().tolist(),
        reverse=True,
    )
    if now.year not in available_years:
        available_years.insert(0, now.year)

    year_column, month_column, _, _ = st.columns(4)
    with year_column:
        selected_year = st.selectbox(
            "Tahun Laporan:",
            options=available_years,
            index=available_years.index(now.year),
            key="report_year",
        )
    with month_column:
        month_names = list(INDONESIAN_MONTHS.values())
        selected_month_name = st.selectbox(
            "Bulan Laporan:",
            options=month_names,
            index=now.month - 1,
            key="report_month",
        )
    selected_month = month_names.index(selected_month_name) + 1

    category_tables, recap_df = build_report_data(
        df,
        selected_year,
        selected_month,
    )

    for category in KPI_LABELS:
        st.markdown(f"#### {category}")
        category_df = category_tables[category]
        if category_df.empty:
            st.info("Tidak ada data pada kategori ini")
        else:
            st.dataframe(
                category_df,
                use_container_width=True,
                hide_index=True,
            )

    st.markdown("#### Rekap SLA")
    st.dataframe(
        recap_df,
        use_container_width=True,
        hide_index=True,
    )

    workbook = create_report_workbook(category_tables, recap_df)
    st.download_button(
        "Download Report Excel",
        data=workbook,
        file_name=(
            f"Report_CR_JR_{selected_year}_"
            f"sampai_{selected_month:02d}.xlsx"
        ),
        mime=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
        key="download_report_excel",
    )


def render_salary_tab():
    st.subheader("Gaji")
    st.caption(
        "Fitur Gaji akan segera hadir. Nantikan update selanjutnya!"
    )


def render_tabs(df, selected_year):
    active_df = prepare_active_data(df)
    analytics_df = prepare_analytics_data(df, selected_year)
    tab1, tab2, tab3, tab4 = st.tabs(TAB_LIST)

    with tab1:
        render_active_tab(active_df)

    with tab2:
        render_analytics_tab(analytics_df, df, selected_year)

    with tab3:
        render_report_tab(df)

    with tab4:
        render_salary_tab()


def render_footer():
    st.caption(
        "Data tersinkronisasi otomatis setiap 10 menit. "
        "Mohon tunggu beberapa saat jika ada perubahan yang belum muncul"
    )
    st.markdown("---")
    st.caption("JoniDep Analytics")


# =============================================================================
# APPLICATION
# =============================================================================

def main():
    st.set_page_config(page_title="Dashboard ITBP", layout="wide")
    initialize_session_state()

    client = get_openai_client()
    st.title("Dashboard CR & JR — ITBP")

    try:
        df, sheet_last_modified = load_data(
            SPREADSHEET_URL,
            SHEET_NAME,
        )
    except Exception as error:
        st.error(f"Error loading dashboard data: {error}")
        df = pd.DataFrame()
        sheet_last_modified = None

    if not df.empty:
        df = prepare_base_data(df)

    selected_year = render_sidebar(client, df)

    if df.empty:
        st.warning("The sheet is empty or did not return any rows.")
        metrics_df = pd.DataFrame()
    else:
        st.caption(f"🕒 **Last Update:** {sheet_last_modified}")
        metrics_df = prepare_metrics_data(df, selected_year)

    if not metrics_df.empty:
        render_kpi_cards(summarize_kpis(metrics_df))

    render_tabs(df, selected_year)
    render_footer()


if __name__ == "__main__":
    main()
