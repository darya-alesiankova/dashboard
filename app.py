"""
US Transaction Pass Rate Calendar
==================================
Показывает проходимость транзакций по дням для юзеров из US за последние 6 месяцев.
Накладывает даты Social Security выплат чтобы видеть "социальные" банки.

Фильтры:
  - Тип транзакции: first_trans / token_trans / subscription_trans
  - Банки: мультиселект, отсортированы по объёму транзакций (desc)

Визуализация:
  - Heatmap: банки × даты, цвет = pass rate %
  - Line chart: динамика pass rate выбранных банков с маркерами SS-дат
  - Сводная таблица: банк → avg pass rate + объём + пик в SS-дни
"""

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from datetime import date, timedelta
import calendar
import os

st.set_page_config(page_title="US Pass Rate Calendar", layout="wide")
st.title("US Transaction Pass Rate Calendar")
st.caption(
    "Проходимость транзакций по дням для US-пользователей. "
    "Вертикальные линии / ячейки с рамкой — даты Social Security выплат."
)


# ─── SS payment dates ────────────────────────────────────────────────────────

# Федеральные праздники США (фиксированные даты с учётом переноса на пн/пт)
US_FEDERAL_HOLIDAYS = {
    # 2025
    date(2025, 11, 11),  # Veterans Day
    date(2025, 11, 27),  # Thanksgiving
    date(2025, 12, 25),  # Christmas
    # 2026
    date(2026, 1,  1),   # New Year's Day
    date(2026, 1,  19),  # MLK Day
    date(2026, 2,  16),  # Presidents Day
    date(2026, 5,  25),  # Memorial Day
    date(2026, 7,  3),   # Independence Day (observed, т.к. 4 июля = сб)
    date(2026, 9,  7),   # Labor Day
    date(2026, 10, 12),  # Columbus Day
    date(2026, 11, 11),  # Veterans Day
    date(2026, 11, 26),  # Thanksgiving
    date(2026, 12, 25),  # Christmas
}


def preceding_banking_day(d: date) -> date:
    """Если дата — выходной или праздник, сдвигает на предыдущий рабочий день."""
    while d.weekday() >= 5 or d in US_FEDERAL_HOLIDAYS:  # сб=5, вс=6
        d -= timedelta(days=1)
    return d


def get_nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """Возвращает n-й день недели (0=пн … 6=вс) в месяце (n=1,2,3,4)."""
    first = date(year, month, 1)
    delta = (weekday - first.weekday()) % 7
    first_occ = first + timedelta(days=delta)
    return first_occ + timedelta(weeks=n - 1)


def get_ss_dates(start: date, end: date) -> dict[date, list[str]]:
    """
    Возвращает dict {date: [label, ...]} для дат SS-выплат в периоде.
    Даты выходного/праздника сдвигаются на предыдущий рабочий день.
    """
    result: dict[date, list[str]] = {}

    def add(d: date, label: str):
        d = preceding_banking_day(d)
        if start <= d <= end:
            result.setdefault(d, []).append(label)

    year, month = start.year, start.month
    while date(year, month, 1) <= end:
        add(date(year, month, 1), "SSI (1st)")
        add(date(year, month, 3), "SS old (3rd)")
        add(get_nth_weekday(year, month, 2, 2), "SS 2nd Wed (1–10)")
        add(get_nth_weekday(year, month, 2, 3), "SS 3rd Wed (11–20)")
        add(get_nth_weekday(year, month, 2, 4), "SS 4th Wed (21–31)")

        month += 1
        if month > 12:
            month = 1
            year += 1

    return result


# ─── Load data from CSV ──────────────────────────────────────────────────────

DATA_PATH = os.path.join(os.path.dirname(__file__), "data", "us_pass_rate.csv")

@st.cache_data(show_spinner=False)
def load_data(trans_types: tuple[str, ...]) -> pd.DataFrame:
    df = pd.read_csv(DATA_PATH)
    df["txn_date"] = pd.to_datetime(df["txn_date"]).dt.date
    df = df[df["type"].isin(trans_types)]
    df = df.groupby(["txn_date", "bank"], as_index=False).agg(
        total=("total", "sum"), success=("success", "sum")
    )
    df["pass_rate"] = (df["success"] / df["total"] * 100).round(1)
    return df


# ─── Sidebar filters ─────────────────────────────────────────────────────────

st.sidebar.header("Фильтры")

type_options = {
    "Card":         ("first_trans", "other_trans"),
    "Token":        ("token_trans",),
    "Subscription": ("subscription_trans",),
}
selected_type_labels = st.sidebar.multiselect(
    "Тип транзакции",
    options=list(type_options.keys()),
    default=list(type_options.keys()),
)
selected_types = tuple(
    t for l in (selected_type_labels or list(type_options.keys()))
    for t in type_options[l]
)

min_volume = st.sidebar.number_input(
    "Мин. транзакций за период (для отображения банка)",
    min_value=1, value=50, step=10
)

with st.spinner("Загружаю данные из BigQuery..."):
    df_raw = load_data(selected_types)

if df_raw.empty:
    st.warning("Нет данных по выбранным фильтрам.")
    st.stop()

# Считаем суммарный объём по банкам → сортируем
bank_volume = df_raw.groupby("bank")["total"].sum().sort_values(ascending=False)
banks_filtered = bank_volume[bank_volume >= min_volume].index.tolist()

selected_banks = st.sidebar.multiselect(
    "Банки (отсортированы по объёму ↓)",
    options=banks_filtered,
    default=banks_filtered[:20],   # по умолчанию топ-20
)

if not selected_banks:
    st.warning("Выберите хотя бы один банк.")
    st.stop()

# Даты SS выплат
date_range_start = df_raw["txn_date"].min()
date_range_end = df_raw["txn_date"].max()
ss_dates = get_ss_dates(date_range_start, date_range_end)
ss_date_set = set(ss_dates.keys())

df = df_raw[df_raw["bank"].isin(selected_banks)].copy()

# ─── Сводная таблица ─────────────────────────────────────────────────────────

st.subheader("Сводка по банкам")

summary = (
    df.groupby("bank")
    .agg(
        total_trans=("total", "sum"),
        success_trans=("success", "sum"),
    )
    .assign(avg_pass_rate=lambda x: (x["success_trans"] / x["total_trans"] * 100).round(1))
    .reset_index()
)

# Pass rate в SS-дни vs обычные дни
df_ss = df[df["txn_date"].isin(ss_date_set)]
df_non = df[~df["txn_date"].isin(ss_date_set)]

ss_agg = (
    df_ss.groupby("bank")
    .agg(ss_total=("total", "sum"), ss_success=("success", "sum"))
    .assign(ss_pass_rate=lambda x: (x["ss_success"] / x["ss_total"] * 100).round(1))
    .reset_index()
)
non_agg = (
    df_non.groupby("bank")
    .agg(non_total=("total", "sum"), non_success=("success", "sum"))
    .assign(non_pass_rate=lambda x: (x["non_success"] / x["non_total"] * 100).round(1))
    .reset_index()
)

summary = (
    summary
    .merge(ss_agg[["bank", "ss_pass_rate", "ss_total"]], on="bank", how="left")
    .merge(non_agg[["bank", "non_pass_rate"]], on="bank", how="left")
)
summary["delta_ss"] = (summary["ss_pass_rate"] - summary["non_pass_rate"]).round(1)
summary = summary.sort_values("total_trans", ascending=False)

styled_summary = (
    summary.rename(columns={
        "bank": "Банк",
        "total_trans": "Всего транзакций",
        "avg_pass_rate": "Avg pass rate %",
        "ss_pass_rate": "Pass rate в SS-дни %",
        "non_pass_rate": "Pass rate в обычные дни %",
        "delta_ss": "Delta (SS − обычные)",
        "ss_total": "Транзакций в SS-дни",
    })
    .style
    .format({
        "Avg pass rate %": "{:.1f}%",
        "Pass rate в SS-дни %": "{:.1f}%",
        "Pass rate в обычные дни %": "{:.1f}%",
        "Delta (SS − обычные)": "{:+.1f}%",
        "Всего транзакций": "{:,}",
        "Транзакций в SS-дни": "{:,}",
    }, na_rep="—")
    .background_gradient(cmap="RdYlGn", subset=["Avg pass rate %", "Pass rate в SS-дни %", "Pass rate в обычные дни %"], axis=None, vmin=50, vmax=100)
    .background_gradient(cmap="RdYlGn", subset=["Delta (SS − обычные)"], axis=None, vmin=-10, vmax=10)
)
st.dataframe(styled_summary, use_container_width=True, height=400)

st.caption(
    "**Delta > 0** → банк лучше проходит в SS-дни (деньги пришли, карта активна). "
    "**Delta < 0** → хуже проходит в SS-дни. "
    "Социальные банки обычно имеют положительную дельту."
)

st.divider()

# ─── Heatmap: банки × даты ───────────────────────────────────────────────────

st.subheader("Heatmap: pass rate по банкам и датам")

# Строим pivot
all_dates = sorted(df["txn_date"].unique())
pivot = df.pivot_table(
    index="bank", columns="txn_date", values="pass_rate", aggfunc="mean"
)
# Сортируем банки по объёму (как в фильтре)
bank_order = [b for b in bank_volume.index if b in pivot.index]
pivot = pivot.reindex(bank_order)

# SS-даты для подсветки
ss_indices = [i for i, d in enumerate(pivot.columns) if d in ss_date_set]
date_labels = [str(d) for d in pivot.columns]

# Добавляем маркер SS-дат в подписи
date_labels_marked = [
    f"★ {d}" if d in ss_date_set else str(d)
    for d in pivot.columns
]

fig_heatmap = go.Figure(
    go.Heatmap(
        z=pivot.values,
        x=date_labels_marked,
        y=pivot.index.tolist(),
        colorscale="RdYlGn",
        zmin=50,
        zmax=100,
        colorbar=dict(title="Pass rate %", ticksuffix="%"),
        hovertemplate="<b>%{y}</b><br>%{x}<br>Pass rate: %{z:.1f}%<extra></extra>",
        xgap=1,
        ygap=1,
    )
)

fig_heatmap.update_layout(
    height=max(400, len(selected_banks) * 22 + 150),
    xaxis=dict(
        title="Дата (★ = SS-выплата)",
        tickangle=-90,
        tickfont=dict(size=9),
        side="bottom",
    ),
    yaxis=dict(title="Банк", autorange="reversed", tickfont=dict(size=10)),
    margin=dict(l=10, r=10, t=40, b=100),
)
st.plotly_chart(fig_heatmap, use_container_width=True)

st.divider()

# ─── Line chart: топ банков по объёму с SS-маркерами ─────────────────────────

st.subheader("Динамика pass rate — топ банков")

top_n = st.slider("Показать топ-N банков по объёму", min_value=1, max_value=min(20, len(selected_banks)), value=min(10, len(selected_banks)))
top_banks = bank_volume[bank_volume.index.isin(selected_banks)].head(top_n).index.tolist()

df_top = df[df["bank"].isin(top_banks)].copy()

# Агрегируем по дате+банк (может быть несколько типов)
df_top_agg = (
    df_top.groupby(["txn_date", "bank"])
    .agg(total=("total", "sum"), success=("success", "sum"))
    .assign(pass_rate=lambda x: (x["success"] / x["total"] * 100).round(1))
    .reset_index()
)
df_top_agg["txn_date"] = pd.to_datetime(df_top_agg["txn_date"])

fig_line = go.Figure()

colors = px.colors.qualitative.Plotly + px.colors.qualitative.Set2
for i, bank in enumerate(top_banks):
    d = df_top_agg[df_top_agg["bank"] == bank]
    fig_line.add_trace(go.Scatter(
        x=d["txn_date"],
        y=d["pass_rate"],
        mode="lines+markers",
        name=bank,
        line=dict(color=colors[i % len(colors)], width=1.5),
        marker=dict(size=4),
        hovertemplate=f"<b>{bank}</b><br>%{{x|%Y-%m-%d}}<br>Pass rate: %{{y:.1f}}%<extra></extra>",
    ))

# SS-даты как вертикальные линии
for ss_date, labels in ss_dates.items():
    x_ms = pd.Timestamp(ss_date).value / 1e6  # ms для plotly shapes
    fig_line.add_shape(
        type="line",
        xref="x", yref="paper",
        x0=x_ms, x1=x_ms,
        y0=0, y1=1,
        line=dict(color="rgba(0,80,200,0.35)", width=1, dash="dot"),
    )
    if "SSI" in " ".join(labels):
        fig_line.add_annotation(
            x=x_ms, y=1.02,
            xref="x", yref="paper",
            text="SS",
            showarrow=False,
            font=dict(size=7, color="rgba(0,80,200,0.7)"),
            xanchor="center",
        )

fig_line.update_layout(
    height=500,
    xaxis_title="Дата",
    yaxis_title="Pass rate %",
    yaxis_ticksuffix="%",
    yaxis_range=[0, 105],
    hovermode="x unified",
    legend=dict(orientation="v", x=1.02, y=1),
    margin=dict(r=180),
)
st.plotly_chart(fig_line, use_container_width=True)

# ─── SS-даты справочник ──────────────────────────────────────────────────────

with st.expander("Даты Social Security выплат в периоде"):
    ss_df = pd.DataFrame([
        {"Дата": str(d), "Тип выплаты": ", ".join(labels)}
        for d, labels in sorted(ss_dates.items())
    ])
    st.dataframe(ss_df, use_container_width=True, hide_index=True)
    st.caption(
        "SSI (1st) — Supplemental Security Income, 1-е число. "
        "SS old (3rd) — получатели SS до мая 1997 или SS+SSI, 3-е число. "
        "SS 2nd/3rd/4th Wed — выплаты по дате рождения."
    )
