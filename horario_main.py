import streamlit as st
import pandas as pd
import unicodedata
import re
from datetime import datetime, time
from itertools import product
from collections import defaultdict
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import csv
import os
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# Configuración del registro
ANALYTICS_DB = "schedule_analytics.csv"
ANALYTICS_HEADERS = [
    "timestamp",
    "ranking_docentes",
    "veto_docentes",
    "preferencia_horario",
    "slider_ranking_docentes",
    "slider_ventana_pausa",
    "slider_dias_libres",
    "Slider_veto_docente",
    "slider_ventana_horaria"
]

def init_analytics_db():
    """Inicializa el archivo de analytics si no existe"""
    if not os.path.exists(ANALYTICS_DB):
        with open(ANALYTICS_DB, mode='w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=ANALYTICS_HEADERS)
            writer.writeheader()

def save_to_google_sheets(data):
    try:
        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive"
        ]
        creds = ServiceAccountCredentials.from_json_keyfile_dict(st.secrets["google"], scope)
        client = gspread.authorize(creds)
        
        sheet = client.open_by_key(st.secrets["google"]["spreadsheet_id"])
        worksheet = sheet.sheet1
        
        # Conversión segura a strings
        def safe_join(items, delimiter="|"):
            if not items:
                return ""
            return delimiter.join(str(item) for item in items)
        
        row = [
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            safe_join(data.get("preferred_teachers", [])),
            safe_join(data.get("banned_teachers", [])),
            str(data.get("time_prefs", {})),
            str(data.get("weights", {}).get("rank", "")),
            str(data.get("weights", {}).get("win", "")),
            str(data.get("weights", {}).get("off", "")),
            str(data.get("weights", {}).get("veto", "")),
            str(data.get("weights", {}).get("window", ""))
        ]
        
        worksheet.append_row(row)
        return True
        
    except Exception as e:
        print(f"Error detallado: {str(e)}")
        return False

# --- Página ---
st.set_page_config(layout="wide")
st.title("Generador de Horarios Duoc")
st.subheader("Creado por Federico Pereira\nfedericopereirazz@gmail.com")

# --- Helpers ---
def strip_accents(s: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFD', s)
                   if unicodedata.category(c) != 'Mn')

# Pattern to extract times (HH:MM or HH:MM:SS)
time_pattern = r"(\d{1,2}:\d{2}(?::\d{2})?)"
DAY_FULL = {
    "Lu":"Lu","Ma":"Ma","Mi":"Mi","Ju":"Ju","Vi":"Vi","Sa":"Sa","Do":"Do",
    "Lunes":"Lu","Martes":"Ma","Miércoles":"Mi","Miercoles":"Mi",
    "Jueves":"Ju","Viernes":"Vi","Sábado":"Sa","Sabado":"Sa","Domingo":"Do"
}

class Section:
    def __init__(self, cid, course, meetings, teacher):
        self.cid = cid
        self.course = course
        self.meetings = meetings
        self.teacher = teacher
    def __str__(self):
        times = "; ".join(f"{d} {s.strftime('%H:%M')}-{e.strftime('%H:%M')}" for d,s,e in self.meetings)
        return f"[{self.cid}] {self.course} — {times} — {self.teacher}"

@st.cache_data
def build_sections(df: pd.DataFrame, id_col: str = 'Sección'):
    by_sec = defaultdict(list)
    for _, row in df.iterrows():
        sec_id = row.get(id_col)
        if pd.isna(sec_id): continue
        by_sec[str(sec_id)].append(row)
    sections = []
    for sec_id, rows in by_sec.items():
        meetings = []
        for r in rows:
            raw = r.get('Horario','')
            parts = raw.split()
            if not parts: continue
            day_raw = parts[0]
            d = DAY_FULL.get(day_raw,None)
            times = re.findall(time_pattern, raw)
            if d and len(times)>=2:
                fmt0 = "%H:%M:%S" if times[0].count(':')==2 else "%H:%M"
                fmt1 = "%H:%M:%S" if times[1].count(':')==2 else "%H:%M"
                try:
                    s = datetime.strptime(times[0],fmt0).time()
                    e = datetime.strptime(times[1],fmt1).time()
                    meetings.append((d,s,e))
                except:
                    pass
        meetings = list(dict.fromkeys(meetings))
        course  = rows[0].get('Asignatura','')
        teacher = rows[0].get('Docente','')
        sections.append(Section(sec_id, course, meetings, teacher))
    return sections

# --- Scheduling Logic ---
def overlaps(a: Section, b: Section) -> bool:
    for d1,s1,e1 in a.meetings:
        for d2,s2,e2 in b.meetings:
            if d1==d2 and s1<e2 and s2<e1:
                return True
    return False

def compute_window(combo):
    max_gap = 0
    by_day = defaultdict(list)
    for sec in combo:
        for d,s,e in sec.meetings:
            by_day[d].append((s,e))
    for ms in by_day.values():
        ms.sort(key=lambda x:x[0])
        for i in range(len(ms)-1):
            gap = (ms[i+1][0].hour*60+ms[i+1][0].minute) - (ms[i][1].hour*60+ms[i][1].minute)
            max_gap = max(max_gap, gap)
    return max_gap

def compute_schedules(courses, ranking, min_free, banned,
                      pref_start: time, pref_end: time, weights):
    hard_window = (weights['window'] == 5)
    hard_veto   = (weights['veto']   == 5)
    hard_off    = (weights['off']    == 5)
    hard_win    = (weights['win']    == 5)
    hard_rank   = (weights['rank']   == 5)

    combos = list(product(*courses.values()))
    raw = []
    if sub:
        for combo in combos:
            # solapamientos
            if any(overlaps(a,b) for a in combo for b in combo if a!=b): continue
            # días libres
            days_occ = {d for sec in combo for d,_,_ in sec.meetings}
            free_days = 5 - len(days_occ)
            if free_days < min_free: continue
            # vetos
            veto_cnt = sum(sec.teacher in banned for sec in combo)
            if hard_veto and veto_cnt>0: continue
            # ventana horaria
            vio = sum(1 for sec in combo for _,s,e in sec.meetings if s<pref_start or e>pref_end)
            if hard_window and vio>0: continue

            # métricas básicas
            avg_rank = sum(ranking.get(sec.teacher,len(ranking)) for sec in combo)/len(combo)
            win_gap  = compute_window(combo)

            raw.append((combo, avg_rank, win_gap, free_days, veto_cnt, vio))

    if not raw: return []

    # si hard_win, filtrar solo el gap mínimo
    if hard_win:
        min_gap = min(r[2] for r in raw)
        raw = [r for r in raw if r[2] == min_gap]
    # si hard_off, filtrar solo los que cumplen EXACTO free_days==min_free
    if hard_off:
        raw = [r for r in raw if r[3] == min_free]
    # si hard_rank, filtrar solo el mejor avg_rank (mínimo)
    if hard_rank:
        best_rank = min(r[1] for r in raw)
        raw = [r for r in raw if r[1] == best_rank]

    # ahora normalizamos y puntuamos
    mx = {
        'rank': max(r[1] for r in raw) or 1,
        'win' : max(r[2] for r in raw) or 1,
        'off' : max(r[3] for r in raw) or 1,
        'veto': max(r[4] for r in raw) or 1,
        'window': max(r[5] for r in raw) or 1
    }
    total_w = sum(weights.values())
    scored = []
    for combo, avg, gap, free, veto, vio in raw:
        n = {
            'rank':   1 - (avg / mx['rank']),
            'win':    1 - (gap / mx['win']),
            'off':    free / mx['off'],
            'veto':   1 - (veto / mx['veto']),
            'window': 1 - (vio  / mx['window'])
        }
        score = sum(weights[k] * n[k] for k in weights) / total_w
        scored.append((score, combo))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored

# --- Visualization ---
def visualize(combo):
    DAY_MAP = {'Lu':0,'Ma':1,'Mi':2,'Ju':3,'Vi':4}
    labels = ["Lunes","Martes","Mié","Jue","Vie"]
    fig, ax = plt.subplots(figsize=(10,6))

    # —————— Tu gráfico normal ——————
    ax.set_xticks([i+0.5 for i in range(5)])
    ax.set_xticklabels(labels)
    ax.set_xlim(0,5)
    ax.set_ylim(20,8)
    ax.set_ylabel("Hora")
    ax.grid(True, which='both', linestyle='--', linewidth=0.5)

    cmap = {} ; colors = plt.cm.tab20.colors
    for sec in combo:
        if not sec.meetings: continue
        for d, s, e in sec.meetings:
            if d not in DAY_MAP: continue
            x = DAY_MAP[d]
            y0 = s.hour + s.minute/60
            h  = (e.hour + e.minute/60) - y0
            if h <= 0: continue
            c = cmap.setdefault(sec.course, colors[len(cmap)%len(colors)])
            ax.add_patch(patches.Rectangle(
                (x+0.05, y0), 0.9, h,
                facecolor=c, edgecolor='black', alpha=0.6))
            ax.text(
                x+0.5, y0 + h/2,
                sec.cid,
                ha='center', va='center', fontsize=7
            )

    fig.text(
        0.95, 0.02,                         
        "Federico Pereira\nfe.pereira@duocuc.cl",
        ha="right", va="bottom",
        fontsize=8,
        color="gray",
        alpha=0.5
    )

    st.pyplot(fig)

# --- UI: cargar CSV ---
csv_url = st.sidebar.text_input(
    "URL GitHub :",
    "https://github.com/federico-pereira/horario_duoc"
)
try:
    df = pd.read_csv("https://raw.githubusercontent.com/federico-pereira/horario_duoc/main/full.csv")
    st.sidebar.success("✅ CSV cargado desde GitHub")
except:
    uploaded = st.sidebar.file_uploader("O sube tu CSV local", type="csv")
    if not uploaded: st.stop()
    df = pd.read_csv(uploaded)

# -- Detectar columnas --
find_col = lambda df, opts: next((c for c in df.columns if c.lower() in [o.lower() for o in opts]), None)
carrera_col = find_col(df, ["Carrera"])
plan_col    = find_col(df, ["Plan"])
jorn_col    = find_col(df, ["Jornada"])
nivel_col   = find_col(df, ["Nivel"])
course_col  = find_col(df, ["Asignatura"])
id_col      = find_col(df, ["Sección","Seccion","SSEC"])
sched_col   = find_col(df, ["Horario"])
teacher_col = find_col(df, ["Docente","Profesor"])

# --- Filtros de metadata ---
if carrera_col:
    carrera = st.sidebar.selectbox("Carrera", sorted(df[carrera_col].dropna().unique()))
    df = df[df[carrera_col]==carrera]
if plan_col:
    plan = st.sidebar.selectbox("Plan", sorted(df[plan_col].dropna().unique()))
    df = df[df[plan_col]==plan]
if jorn_col:
    jorn = st.sidebar.selectbox("Jornada", sorted(df[jorn_col].dropna().unique()))
    df = df[df[jorn_col]==jorn]
if nivel_col:
    niveles = [v for v in sorted(df[nivel_col].unique()) if str(v).isdigit()]
    niv = st.sidebar.selectbox("Nivel", niveles)
    df = df[(df[nivel_col]==niv) | (df[nivel_col].str.lower()=="optativos")]

# --- Construir secciones y cursos ---
secs    = build_sections(df, id_col)
courses = defaultdict(list)
for sec in secs:
    courses[sec.course].append(sec)

# --- Sidebar: asignaturas, ranking y vetos ---
sel = st.sidebar.multiselect("Asignaturas a incluir", sorted(courses), default=None)
sub = {c:courses[c] for c in sel}
raw_teachers = {sec.teacher for secs in sub.values() for sec in secs}
teachers = sorted(str(t) for t in raw_teachers if pd.notna(t))
ranking_sel = st.sidebar.multiselect("Ranking docentes (mejor primero)", teachers, default=None)
ranking_map = {t:i for i,t in enumerate(ranking_sel)}
banned      = st.sidebar.multiselect("Docentes vetados", teachers)

# --- Preferencia horaria y pesos ---
pref_start = st.sidebar.time_input("Desde", time(8,30))
pref_end   = st.sidebar.time_input("Hasta", time(16,0))
min_free   = st.sidebar.slider("Días libres mínimos", 0, 5, 0)
st.sidebar.header("Importancia de variables 5 = si o si")
weights    = {
    'rank':   st.sidebar.slider("Peso ranking docente",   1.0, 5.0, 3.0),
    'win':    st.sidebar.slider("Peso ventana pausa",     1.0, 5.0, 3.0),
    'off':    st.sidebar.slider("Peso días libres",       1.0, 5.0, 3.0),
    'veto':   st.sidebar.slider("Peso veto docente",      1.0, 5.0, 3.0),
    'window': st.sidebar.slider("Peso ventana horaria",   1.0, 5.0, 3.0)
}

# --- Estado y generación ---
if 'scored' not in st.session_state: st.session_state.scored = []
if 'selected_idx' not in st.session_state: st.session_state.selected_idx = 0
if 'generation_attempted' not in st.session_state:
    st.session_state.generation_attempted = False

def generate():
    st.session_state.generation_attempted = True
    st.session_state.scored = compute_schedules(
        sub, ranking_map, min_free, banned,
        pref_start, pref_end, weights
    )
    if st.session_state.scored:
        st.session_state.selected_idx = 0

st.sidebar.button("Generar horarios", on_click=generate, key="gen_button")


# --- Mostrar top5 como 5 botones ---
if st.session_state.scored:

    save_to_google_sheets({
        "preferred_teachers": [str(t) for t in ranking_sel],  # Convierte a strings
        "banned_teachers": [str(t) for t in banned],         # Convierte a strings
        "time_prefs": {
            "hora_inicio": pref_start.strftime("%H:%M"),
            "hora_fin": pref_end.strftime("%H:%M"),
            "dias_libres": min_free
        },
        "weights": weights
    })

    st.sidebar.markdown("### Elige una solución:")
    top5 = st.session_state.scored[:5]
    for i,(score,_) in enumerate(top5):
        st.sidebar.button(
            f"Solución {i+1} ({score:.2f})",
            key=f"sol_btn_{i}",
            on_click=lambda i=i: st.session_state.update(selected_idx=i)
        )
    # Renderizar solución seleccionada
    idx = st.session_state.selected_idx
    score, combo = top5[idx]
    st.subheader(f"Solución {idx+1} (score: {score:.2f})")
    for sec in combo:
        st.write(sec)
    st.write("### Gráfico de la solución")
    visualize(combo)
elif(not sub):
    st.info("Introduzca datos antes de pulsar **Generar horarios** .")
elif st.session_state.generation_attempted: 
    st.warning("No se encontraron soluciones válidas.")
