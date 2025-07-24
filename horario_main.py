import streamlit as st
import pandas as pd
import unicodedata
import re
from datetime import datetime, time
from itertools import product
from collections import defaultdict
import matplotlib.pyplot as plt
import matplotlib.patches as patches

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
    hard_window = (weights['window']==5)
    hard_veto   = (weights['veto']==5)
    combos = list(product(*courses.values()))
    metrics = []
    for combo in combos:
        if any(overlaps(a,b) for a in combo for b in combo if a!=b): continue
        days_occ = {d for sec in combo for d,_,_ in sec.meetings}
        if (5-len(days_occ))<min_free: continue
        veto_cnt = sum(sec.teacher in banned for sec in combo)
        if hard_veto and veto_cnt>0: continue
        vio = sum(1 for sec in combo for _,s,e in sec.meetings if s<pref_start or e>pref_end)
        if hard_window and vio>0: continue
        avg_rank = sum(ranking.get(sec.teacher,len(ranking)) for sec in combo)/len(combo)
        win_gap  = compute_window(combo)
        free_days=5-len(days_occ)
        metrics.append((combo,avg_rank,win_gap,free_days,veto_cnt,vio))
    if not metrics: return []
    mx = {i: max(vals) or 1 for i, vals in enumerate(zip(*[m[1:] for m in metrics]))}
    total_w = sum(weights.values())
    scored = []
    for combo,avg,gap,free,veto,vio in metrics:
        n = {
            'rank':   1 - avg/mx[0],
            'win':    1 - gap/mx[1],
            'off':    free/mx[2],
            'veto':   1 - veto/mx[3],
            'window': 1 - vio/mx[4]
        }
        score = sum(weights[k]*n[k] for k in weights)/total_w
        scored.append((score,combo))
    scored.sort(key=lambda x:x[0], reverse=True)
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
    "URL raw GitHub CSV:",
    "https://raw.githubusercontent.com/federico-pereira/horario_duoc/main/full.csv"
)
try:
    df = pd.read_csv(csv_url)
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

def generate():
    st.session_state.scored = compute_schedules(
        sub, ranking_map, min_free, banned,
        pref_start, pref_end, weights
    )
    st.session_state.selected_idx = 0

st.sidebar.button("Generar horarios", on_click=generate, key="gen_button")

# --- Mostrar top5 como 5 botones ---
if st.session_state.scored:
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
else:
    st.info("Introduzca datos antes de pulsar **Generar horarios** .")
