import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from google.cloud import bigquery

st.set_page_config(page_title="Refunds Dashboard", layout="wide")
st.title("Refunds")

PROJECT_ID = 'asocial-prod'


@st.cache_data(ttl=1800)
def load_refunds_after_sub():
    client = bigquery.Client(project=PROJECT_ID)
    sql = """
    WITH paying AS (
        SELECT id_user, DATE_TRUNC(user_registration, MONTH) AS reg_month
        FROM `asocial-prod.analytics.transactions`
        WHERE status = 'success' AND user_registration >= '2025-01-01'
        GROUP BY 1, 2
    ),
    all_sub_attempts AS (
        SELECT id_user, DATE(date_created) AS sub_date,
            CASE WHEN status = 'success' THEN 'success' ELSE 'fail' END AS sub_status
        FROM `asocial-prod.analytics.transactions`
        WHERE transaction_type = 'subscription'
          AND user_registration >= '2025-01-01'
    ),
    first_payment AS (
        SELECT id_user, MIN(DATE(date_created)) AS first_payment_date
        FROM `asocial-prod.analytics.transactions`
        WHERE status = 'success' AND user_registration >= '2025-01-01'
        GROUP BY 1
    ),
    first_refund AS (
        SELECT user_id, MIN(refund_date) AS first_refund_date
        FROM `asocial-prod.analytics.refunds`
        WHERE refund_date IS NOT NULL GROUP BY 1
    ),
    last_attempt AS (
        SELECT fr.user_id,
            MAX(s.sub_date) AS last_attempt_date,
            MAX(CASE WHEN s.sub_date = (
                SELECT MAX(s2.sub_date) FROM all_sub_attempts s2
                WHERE s2.id_user = fr.user_id AND s2.sub_date <= fr.first_refund_date
            ) THEN s.sub_status END) AS last_attempt_status
        FROM first_refund fr
        JOIN all_sub_attempts s ON fr.user_id = s.id_user AND s.sub_date <= fr.first_refund_date
        GROUP BY 1
    ),
    classified AS (
        SELECT p.reg_month, p.id_user,
            CASE
                WHEN la.last_attempt_date IS NOT NULL AND la.last_attempt_status = 'success' THEN
                    CASE
                        WHEN DATE_DIFF(fr.first_refund_date, la.last_attempt_date, DAY) <= 3  THEN '0–3 дня'
                        WHEN DATE_DIFF(fr.first_refund_date, la.last_attempt_date, DAY) <= 7  THEN '4–7 дней'
                        WHEN DATE_DIFF(fr.first_refund_date, la.last_attempt_date, DAY) <= 14 THEN '8–14 дней'
                        WHEN DATE_DIFF(fr.first_refund_date, la.last_attempt_date, DAY) <= 21 THEN '15–21 день'
                        ELSE '21+ дней'
                    END
                WHEN la.last_attempt_date IS NOT NULL THEN
                    CASE
                        WHEN DATE_DIFF(fr.first_refund_date, la.last_attempt_date, DAY) <= 3  THEN 'fail: 0–3 дня'
                        WHEN DATE_DIFF(fr.first_refund_date, la.last_attempt_date, DAY) <= 7  THEN 'fail: 4–7 дней'
                        WHEN DATE_DIFF(fr.first_refund_date, la.last_attempt_date, DAY) <= 14 THEN 'fail: 8–14 дней'
                        WHEN DATE_DIFF(fr.first_refund_date, la.last_attempt_date, DAY) <= 21 THEN 'fail: 15–21 день'
                        ELSE 'fail: 21+ дней'
                    END
                ELSE
                    CASE
                        WHEN DATE_DIFF(fr.first_refund_date, fp.first_payment_date, DAY) <= 21 THEN 'до подписки'
                        WHEN DATE_DIFF(fr.first_refund_date, fp.first_payment_date, DAY) <= 24 THEN '0–3 дня'
                        WHEN DATE_DIFF(fr.first_refund_date, fp.first_payment_date, DAY) <= 28 THEN '4–7 дней'
                        WHEN DATE_DIFF(fr.first_refund_date, fp.first_payment_date, DAY) <= 35 THEN '8–14 дней'
                        WHEN DATE_DIFF(fr.first_refund_date, fp.first_payment_date, DAY) <= 42 THEN '15–21 день'
                        ELSE '21+ дней'
                    END
            END AS category
        FROM paying p
        JOIN first_refund fr ON p.id_user = fr.user_id
        LEFT JOIN last_attempt la ON p.id_user = la.user_id
        LEFT JOIN first_payment fp ON p.id_user = fp.id_user
    )
    SELECT
        c.reg_month,
        c.category,
        COUNT(DISTINCT c.id_user) AS users,
        ROUND(SUM(r.fee_amount_refund), 2) AS amount
    FROM classified c
    JOIN `asocial-prod.analytics.refunds` r ON c.id_user = r.user_id
    GROUP BY 1, 2 ORDER BY 1, 2
    """
    df = client.query(sql).to_dataframe()
    client.close()
    df['reg_month'] = pd.to_datetime(df['reg_month']).dt.strftime('%Y-%m')

    col_order = ['до подписки',
                 '0–3 дня','4–7 дней','8–14 дней','15–21 день','21+ дней',
                 'fail: 0–3 дня','fail: 4–7 дней','fail: 8–14 дней','fail: 15–21 день','fail: 21+ дней']

    df = df.groupby(['reg_month', 'category']).agg(users=('users', 'sum'), amount=('amount', 'sum')).reset_index()

    col_order = ['до подписки',
                 '0–3 дня','4–7 дней','8–14 дней','15–21 день','21+ дней',
                 'fail: 0–3 дня','fail: 4–7 дней','fail: 8–14 дней','fail: 15–21 день','fail: 21+ дней']

    pivot_users = df.pivot(index='reg_month', columns='category', values='users').fillna(0).astype(int)
    pivot_users = pivot_users[[c for c in col_order if c in pivot_users.columns]]
    pivot_users['Всего'] = pivot_users.sum(axis=1)

    pivot_amount = df.pivot(index='reg_month', columns='category', values='amount').fillna(0).round(0).astype(int)
    pivot_amount = pivot_amount[[c for c in col_order if c in pivot_amount.columns]]
    pivot_amount['Всего'] = pivot_amount.sum(axis=1)

    return pivot_users, pivot_amount



@st.cache_data(ttl=1800)
def load_refunds_by_influence_category():
    client = bigquery.Client(project=PROJECT_ID)
    sql = """
    WITH paying AS (
        SELECT id_user, DATE_TRUNC(user_registration, MONTH) AS reg_month
        FROM `asocial-prod.analytics.transactions`
        WHERE status = 'success' AND user_registration >= '2025-01-01'
        GROUP BY 1, 2
    ),
    all_sub_attempts AS (
        SELECT id_user, DATE(date_created) AS sub_date
        FROM `asocial-prod.analytics.transactions`
        WHERE transaction_type = 'subscription'
          AND user_registration >= '2025-01-01'
    ),
    first_payment AS (
        SELECT id_user, MIN(DATE(date_created)) AS first_payment_date
        FROM `asocial-prod.analytics.transactions`
        WHERE status = 'success' AND user_registration >= '2025-01-01'
        GROUP BY 1
    ),
    first_refund AS (
        SELECT user_id, MIN(refund_date) AS first_refund_date
        FROM `asocial-prod.analytics.refunds`
        WHERE refund_date IS NOT NULL GROUP BY 1
    ),
    last_attempt_before_refund AS (
        SELECT fr.user_id, MAX(s.sub_date) AS last_attempt_before
        FROM first_refund fr
        JOIN all_sub_attempts s ON fr.user_id = s.id_user AND s.sub_date <= fr.first_refund_date
        GROUP BY 1
    ),
    classified AS (
        SELECT p.id_user, p.reg_month,
            CASE
                WHEN fr.first_refund_date IS NULL THEN NULL
                WHEN la.last_attempt_before IS NOT NULL THEN
                    CASE
                        WHEN DATE_DIFF(fr.first_refund_date, la.last_attempt_before, DAY) <= 7  THEN 'влияние подписки (0–7)'
                        WHEN DATE_DIFF(fr.first_refund_date, la.last_attempt_before, DAY) <= 21 THEN 'влияние подписки (8–21)'
                        ELSE 'после подписки (21+)'
                    END
                ELSE
                    CASE
                        WHEN DATE_DIFF(fr.first_refund_date, fp.first_payment_date, DAY) <= 21 THEN 'до подписки'
                        WHEN DATE_DIFF(fr.first_refund_date, fp.first_payment_date, DAY) <= 28 THEN 'влияние подписки (0–7)'
                        WHEN DATE_DIFF(fr.first_refund_date, fp.first_payment_date, DAY) <= 42 THEN 'влияние подписки (8–21)'
                        ELSE 'после подписки (21+)'
                    END
            END AS category
        FROM paying p
        LEFT JOIN first_refund fr ON p.id_user = fr.user_id
        LEFT JOIN last_attempt_before_refund la ON p.id_user = la.user_id
        LEFT JOIN first_payment fp ON p.id_user = fp.id_user
    ),
    user_refund_totals AS (
        SELECT user_id, ROUND(SUM(fee_amount_refund), 2) AS total_amount
        FROM `asocial-prod.analytics.refunds`
        WHERE refund_date IS NOT NULL
        GROUP BY 1
    ),
    paying_counts AS (
        SELECT reg_month, COUNT(DISTINCT id_user) AS paying_users FROM paying GROUP BY 1
    ),
    agg AS (
        SELECT c.reg_month, c.category,
            COUNT(DISTINCT c.id_user) AS refund_users,
            pc.paying_users,
            ROUND(SUM(rt.total_amount), 0) AS total_amount
        FROM classified c
        JOIN paying_counts pc ON c.reg_month = pc.reg_month
        JOIN user_refund_totals rt ON c.id_user = rt.user_id
        WHERE c.category IS NOT NULL
        GROUP BY 1, 2, pc.paying_users
    )
    SELECT reg_month, category, refund_users, paying_users,
        ROUND(refund_users * 100.0 / paying_users, 1) AS pct_users,
        total_amount,
        ROUND(total_amount * 100.0 / SUM(total_amount) OVER (PARTITION BY reg_month), 1) AS pct_amount
    FROM agg ORDER BY 1, 2
    """
    df = client.query(sql).to_dataframe()
    client.close()
    df['reg_month'] = pd.to_datetime(df['reg_month']).dt.strftime('%Y-%m')
    col_order = ['до подписки', 'влияние подписки (0–7)', 'влияние подписки (8–21)', 'после подписки (21+)']

    pct_u = df.pivot(index='reg_month', columns='category', values='pct_users').fillna(0)
    pct_u = pct_u[[c for c in col_order if c in pct_u.columns]]
    paying = df.groupby('reg_month')['paying_users'].first()
    pct_u['итого %'] = (df.groupby('reg_month')['refund_users'].sum() / paying * 100).round(1)
    pct_u['платников'] = paying.astype(int)

    pct_a = df.pivot(index='reg_month', columns='category', values='pct_amount').fillna(0)
    pct_a = pct_a[[c for c in col_order if c in pct_a.columns]]
    pct_a['итого $'] = df.groupby('reg_month')['total_amount'].sum().astype(int)

    amt = df.pivot(index='reg_month', columns='category', values='total_amount').fillna(0).astype(int)
    amt = amt[[c for c in col_order if c in amt.columns]]
    amt['итого $'] = amt.sum(axis=1)

    return pct_u, pct_a, amt


@st.cache_data(ttl=1800)
def load_refunds_by_month():
    client = bigquery.Client(project=PROJECT_ID)
    sql = """
    SELECT
        DATE_TRUNC(refund_date, MONTH) AS refund_month,
        refund_type,
        COUNT(*) AS transactions,
        COUNT(DISTINCT user_id) AS unique_users,
        ROUND(SUM(fee_amount_refund), 2) AS amount
    FROM `asocial-prod.analytics.refunds`
    WHERE refund_date IS NOT NULL
    GROUP BY 1, 2
    ORDER BY 1, 2
    """
    df = client.query(sql).to_dataframe()
    client.close()
    df['refund_month'] = pd.to_datetime(df['refund_month'])
    return df


@st.cache_data(ttl=1800)
def load_refund_pct_by_category():
    client = bigquery.Client(project=PROJECT_ID)
    sql = """
    WITH funnel AS (
        SELECT user_id, category, month_created AS reg_month
        FROM `asocial-prod.analytics.funnel_regular_users`
        WHERE day_after_reg = 'today'
          AND category IS NOT NULL
    ),
    paying_users AS (
        SELECT id_user, DATE_TRUNC(user_registration, MONTH) AS reg_month
        FROM `asocial-prod.analytics.transactions`
        WHERE status = 'success'
        GROUP BY 1, 2
    ),
    base AS (
        SELECT
            CASE WHEN f.reg_month < '2025-01-01' THEN DATE '2024-01-01' ELSE f.reg_month END AS reg_month,
            f.category,
            p.id_user,
            r.user_id AS refund_user
        FROM funnel f
        JOIN paying_users p ON f.user_id = p.id_user AND f.reg_month = p.reg_month
        LEFT JOIN (SELECT DISTINCT user_id FROM `asocial-prod.analytics.refunds`) r ON p.id_user = r.user_id
    )
    SELECT
        reg_month,
        category,
        COUNT(DISTINCT id_user) AS paying_users,
        COUNT(DISTINCT refund_user) AS users_with_refund,
        ROUND(COUNT(DISTINCT refund_user) * 100.0 / COUNT(DISTINCT id_user), 2) AS refund_pct
    FROM base
    GROUP BY 1, 2
    ORDER BY 1, 2
    """
    df = client.query(sql).to_dataframe()
    client.close()
    df['reg_month'] = pd.to_datetime(df['reg_month'])
    df['label'] = df['reg_month'].apply(lambda x: '2024' if x.year == 2024 else x.strftime('%Y-%m'))

    df = df.groupby(['label', 'category']).agg(
        paying_users=('paying_users', 'sum'),
        users_with_refund=('users_with_refund', 'sum')
    ).reset_index()
    df['refund_pct'] = (df['users_with_refund'] / df['paying_users'] * 100).round(2)

    exclude_cats = ['Main', 'Native', 'Bing search']
    big_cats = df.groupby('category')['paying_users'].sum()
    big_cats = big_cats[big_cats >= 100].index.tolist()
    big_cats = [c for c in big_cats if c not in exclude_cats]
    df = df[df['category'].isin(big_cats)]

    pivot_pct = df.pivot(index='category', columns='label', values='refund_pct')
    pivot_users = df.pivot(index='category', columns='label', values='paying_users')
    return pivot_pct, pivot_users


@st.cache_data(ttl=1800)
def load_refund_amount_by_reg_month():
    client = bigquery.Client(project=PROJECT_ID)
    sql = """
    WITH paying_users AS (
        SELECT id_user, DATE_TRUNC(user_registration, MONTH) AS reg_month
        FROM `asocial-prod.analytics.transactions`
        WHERE status = 'success'
        GROUP BY 1, 2
    )
    SELECT
        p.reg_month,
        r.refund_type,
        ROUND(SUM(r.fee_amount_refund), 2) AS amount
    FROM paying_users p
    JOIN `asocial-prod.analytics.refunds` r ON p.id_user = r.user_id
    WHERE r.refund_date IS NOT NULL
    GROUP BY 1, 2
    ORDER BY 1, 2
    """
    df = client.query(sql).to_dataframe()
    client.close()
    df['reg_month'] = pd.to_datetime(df['reg_month'])
    return df


@st.cache_data(ttl=1800)
def load_refund_pct_by_reg_month():
    client = bigquery.Client(project=PROJECT_ID)
    sql = """
    WITH paying_users AS (
        SELECT
            id_user,
            DATE_TRUNC(user_registration, MONTH) AS reg_month
        FROM `asocial-prod.analytics.transactions`
        WHERE status = 'success'
        GROUP BY 1, 2
    ),
    paying_counts AS (
        SELECT reg_month, COUNT(DISTINCT id_user) AS paying_users
        FROM paying_users
        GROUP BY 1
    ),
    refund_counts AS (
        SELECT
            p.reg_month,
            r.refund_type,
            COUNT(DISTINCT r.user_id) AS users_with_refund
        FROM paying_users p
        INNER JOIN (SELECT DISTINCT user_id, refund_type FROM `asocial-prod.analytics.refunds`) r
            ON p.id_user = r.user_id
        GROUP BY 1, 2
    )
    SELECT
        rc.reg_month,
        rc.refund_type,
        pc.paying_users,
        rc.users_with_refund,
        ROUND(rc.users_with_refund * 100.0 / pc.paying_users, 2) AS refund_pct
    FROM refund_counts rc
    JOIN paying_counts pc ON rc.reg_month = pc.reg_month
    ORDER BY 1, 2
    """
    df = client.query(sql).to_dataframe()
    client.close()
    df['reg_month'] = pd.to_datetime(df['reg_month'])
    return df


@st.cache_data(ttl=1800)
def load_refund_by_bucket():
    client = bigquery.Client(project=PROJECT_ID)
    sql = """
    WITH paying AS (
        SELECT id_user, DATE_TRUNC(user_registration, MONTH) AS reg_month,
            MAX(cumulative_fee_amount) AS total_paid
        FROM `asocial-prod.analytics.transactions`
        WHERE status = 'success'
        GROUP BY id_user, DATE_TRUNC(user_registration, MONTH)
    ),
    bucketed AS (
        SELECT id_user, reg_month,
            CASE
                WHEN total_paid < 500  THEN '1. до $500'
                WHEN total_paid < 1000 THEN '2. $500–1000'
                WHEN total_paid < 5000 THEN '3. $1000–5000'
                ELSE '4. $5000+'
            END AS bucket
        FROM paying
    ),
    paying_counts AS (
        SELECT reg_month, bucket, COUNT(DISTINCT id_user) AS paying_users
        FROM bucketed GROUP BY 1, 2
    ),
    refunds_joined AS (
        SELECT b.reg_month, b.bucket, r.refund_type,
            COUNT(DISTINCT b.id_user) AS users_with_refund,
            ROUND(SUM(r.fee_amount_refund), 2) AS total_refund
        FROM bucketed b
        JOIN `asocial-prod.analytics.refunds` r ON b.id_user = r.user_id
        WHERE r.refund_date IS NOT NULL
        GROUP BY 1, 2, 3
    )
    SELECT pc.reg_month, pc.bucket, pc.paying_users,
        r.refund_type,
        COALESCE(r.users_with_refund, 0) AS users_with_refund,
        COALESCE(r.total_refund, 0) AS total_refund
    FROM paying_counts pc
    LEFT JOIN refunds_joined r ON pc.reg_month = r.reg_month AND pc.bucket = r.bucket
    ORDER BY 1, 2, 3
    """
    df = client.query(sql).to_dataframe()
    client.close()
    df['reg_month'] = pd.to_datetime(df['reg_month']).dt.strftime('%Y-%m')
    return df


with st.spinner("Загружаю данные..."):
    df_monthly = load_refunds_by_month()
    df_pct = load_refund_pct_by_reg_month()
    df_category, df_category_users = load_refund_pct_by_category()
    df_after_sub, df_after_sub_amount = load_refunds_after_sub()
    df_inf_pct_u, df_inf_pct_a, df_inf_amt = load_refunds_by_influence_category()
    df_amt_reg = load_refund_amount_by_reg_month()
    df_bucket_raw = load_refund_by_bucket()

# ── Filters ───────────────────────────────────────────────────────────────────
col_f1, col_f2 = st.columns([2, 1])
with col_f1:
    min_date = df_monthly['refund_month'].min()
    max_date = df_monthly['refund_month'].max()
    date_range = st.slider("Период", min_value=min_date.to_pydatetime(),
                           max_value=max_date.to_pydatetime(),
                           value=(min_date.to_pydatetime(), max_date.to_pydatetime()),
                           format="YYYY-MM")
with col_f2:
    types = st.multiselect("refund_type", options=sorted(df_monthly['refund_type'].unique()),
                           default=sorted(df_monthly['refund_type'].unique()))

df_pct_f = df_pct[
    (df_pct['reg_month'] >= date_range[0]) &
    (df_pct['reg_month'] <= date_range[1]) &
    (df_pct['refund_type'].isin(types))
]

df_amt_reg_f = df_amt_reg[
    (df_amt_reg['reg_month'] >= date_range[0]) &
    (df_amt_reg['reg_month'] <= date_range[1]) &
    (df_amt_reg['refund_type'].isin(types))
]

_buckets_order = ['1. до $500', '2. $500–1000', '3. $1000–5000', '4. $5000+']
_paying_per_bucket = df_bucket_raw[['reg_month', 'bucket', 'paying_users']].drop_duplicates()
_bucket_f = df_bucket_raw[df_bucket_raw['refund_type'].isin(types)]
df_bucket_agg = (
    _bucket_f.groupby(['reg_month', 'bucket'])
    .agg(users_with_refund=('users_with_refund', 'sum'), total_refund=('total_refund', 'sum'))
    .reset_index()
    .merge(_paying_per_bucket, on=['reg_month', 'bucket'], how='right')
    .fillna(0)
)
df_bucket_agg['pct_users'] = (df_bucket_agg['users_with_refund'] / df_bucket_agg['paying_users'] * 100).round(2)
df_bucket_agg['avg_refund'] = (df_bucket_agg['total_refund'] / df_bucket_agg['paying_users']).round(2)

# ── KPIs ──────────────────────────────────────────────────────────────────────
c1, c2, c3 = st.columns(3)
c1.metric("Уникальных пользователей с рефандом", f"{df_pct_f['users_with_refund'].sum():,}")
c2.metric("Платников", f"{df_pct_f['paying_users'].sum():,}")
c3.metric("Сумма рефандов", f"${df_amt_reg_f['amount'].sum():,.0f}")

st.divider()

# ── % пользователей с рефандом по месяцу регистрации ─────────────────────────
st.subheader("% пользователей с рефандом / ЧБ — по месяцу регистрации")

df_pct_notnull = df_pct_f[df_pct_f['refund_type'].notna()]

fig_pct = px.line(df_pct_notnull, x='reg_month', y='refund_pct', color='refund_type',
                  markers=True,
                  labels={'reg_month': 'Месяц регистрации', 'refund_pct': '%', 'refund_type': 'тип'})
fig_pct.update_layout(height=380, yaxis_ticksuffix='%', hovermode='x unified')
fig_pct.update_traces(hovertemplate='%{fullData.name}: %{y:.1f}%<extra></extra>')
st.plotly_chart(fig_pct, use_container_width=True)

st.subheader("% пользователей с рефандом+ЧБ — по группам оплат")
_pct_pivot = df_bucket_agg.pivot(index='reg_month', columns='bucket', values='pct_users')
_pct_pivot = _pct_pivot[[c for c in _buckets_order if c in _pct_pivot.columns]]
_totals_pct = df_bucket_agg.groupby('reg_month').agg(u=('users_with_refund', 'sum'), p=('paying_users', 'sum'))
_pct_pivot.insert(0, 'Все', (_totals_pct['u'] / _totals_pct['p'] * 100).round(2))
df_pct_bucket_long = _pct_pivot.reset_index().melt(id_vars='reg_month', var_name='группа', value_name='%')
fig_pct_bucket = px.line(df_pct_bucket_long, x='reg_month', y='%', color='группа',
                         markers=True,
                         labels={'reg_month': 'Месяц регистрации', '%': '%', 'группа': 'группа'})
fig_pct_bucket.update_layout(height=380, yaxis_ticksuffix='%', hovermode='x unified')
fig_pct_bucket.update_traces(hovertemplate='%{fullData.name}: %{y:.1f}%<extra></extra>')
st.plotly_chart(fig_pct_bucket, use_container_width=True)

# ── Суммы рефандов по месяцу регистрации ─────────────────────────────────────
st.subheader("Суммы рефандов ($) — по месяцу регистрации")

fig_amt = px.bar(df_amt_reg_f, x='reg_month', y='amount', color='refund_type',
                 labels={'reg_month': 'Месяц регистрации', 'amount': '$', 'refund_type': 'тип'})
fig_amt.update_layout(height=380)
st.plotly_chart(fig_amt, use_container_width=True)

st.subheader("Средний рефанд+ЧБ на платника — по группам оплат")
_avg_pivot = df_bucket_agg.pivot(index='reg_month', columns='bucket', values='avg_refund')
_avg_pivot = _avg_pivot[[c for c in _buckets_order if c in _avg_pivot.columns]]
_totals_avg = df_bucket_agg.groupby('reg_month').agg(t=('total_refund', 'sum'), p=('paying_users', 'sum'))
_avg_pivot.insert(0, 'Все', (_totals_avg['t'] / _totals_avg['p']).round(2))
df_avg_bucket_long = _avg_pivot.reset_index().melt(id_vars='reg_month', var_name='группа', value_name='$')
fig_avg_bucket = px.line(df_avg_bucket_long, x='reg_month', y='$', color='группа',
                         markers=True,
                         labels={'reg_month': 'Месяц регистрации', '$': '$ на платника', 'группа': 'группа'})
fig_avg_bucket.update_layout(height=380, hovermode='x unified')
fig_avg_bucket.update_traces(hovertemplate='%{fullData.name}: $%{y:,.2f}<extra></extra>')
st.plotly_chart(fig_avg_bucket, use_container_width=True)

st.subheader("Средний рефанд+ЧБ на рефандера — по группам оплат")
df_bucket_agg['avg_per_refunder'] = (
    df_bucket_agg['total_refund'] / df_bucket_agg['users_with_refund'].replace(0, float('nan'))
).round(2)
_ref_pivot = df_bucket_agg.pivot(index='reg_month', columns='bucket', values='avg_per_refunder')
_ref_pivot = _ref_pivot[[c for c in _buckets_order if c in _ref_pivot.columns]]
_totals_ref = df_bucket_agg.groupby('reg_month').agg(t=('total_refund', 'sum'), r=('users_with_refund', 'sum'))
_ref_pivot.insert(0, 'Все', (_totals_ref['t'] / _totals_ref['r'].replace(0, float('nan'))).round(2))
df_ref_long = _ref_pivot.reset_index().melt(id_vars='reg_month', var_name='группа', value_name='$')
fig_ref_bucket = px.line(df_ref_long, x='reg_month', y='$', color='группа',
                         markers=True,
                         labels={'reg_month': 'Месяц регистрации', '$': '$ на рефандера', 'группа': 'группа'})
fig_ref_bucket.update_layout(height=380, hovermode='x unified')
fig_ref_bucket.update_traces(hovertemplate='%{fullData.name}: $%{y:,.2f}<extra></extra>')
st.plotly_chart(fig_ref_bucket, use_container_width=True)

st.divider()

# ── Heatmap: % рефандов по категории и месяцу регистрации ────────────────────
st.subheader("% рефандов по категории трафика и месяцу регистрации")

styled_pct = (
    df_category.style
    .format("{:.1f}%", na_rep="—")
    .background_gradient(cmap="RdYlGn_r", axis=None, vmin=0, vmax=15)
)
st.dataframe(styled_pct, use_container_width=True)

st.subheader("Платники по категории трафика и месяцу регистрации")
styled_users = (
    df_category_users.style
    .format("{:.0f}", na_rep="—")
    .background_gradient(cmap="Blues", axis=None)
)
st.dataframe(styled_users, use_container_width=True)

st.divider()

# ── Рефанды после списания подписки ──────────────────────────────────────────
st.subheader("Рефанды по дням после списания подписки — по месяцам (пользователи с подписками, с мая 2025)")
color_cols = [c for c in df_after_sub.columns if c != 'Всего']
styled_sub = (
    df_after_sub.style
    .format("{:.0f}")
    .background_gradient(cmap="OrRd", axis=None, subset=color_cols)
)
st.dataframe(styled_sub, use_container_width=True)

st.subheader("Суммы рефандов ($) по дням после списания подписки — по месяцу регистрации")
color_cols_a = [c for c in df_after_sub_amount.columns if c != 'Всего']
styled_sub_amount = (
    df_after_sub_amount.style
    .format("{:,.0f}")
    .background_gradient(cmap="OrRd", axis=None, subset=color_cols_a)
)
st.dataframe(styled_sub_amount, use_container_width=True)

st.divider()

# ── Рефанды по категории влияния подписки ────────────────────────────────────
st.divider()
st.subheader("Рефанды по категории влияния подписки — по месяцу регистрации")

cat_cols = ['до подписки', 'влияние подписки (0–7)', 'влияние подписки (8–21)', 'после подписки (21+)']

st.markdown("**% пользователей с рефандом от платников**")
styled_inf_u = (
    df_inf_pct_u.style
    .format({c: '{:.1f}%' for c in [*cat_cols, 'итого %']} | {'платников': '{:.0f}'})
    .background_gradient(cmap="OrRd", axis=None,
                         subset=[c for c in cat_cols if c in df_inf_pct_u.columns])
)
st.dataframe(styled_inf_u, use_container_width=True)

st.markdown("**% суммы рефандов по категориям**")
styled_inf_a = (
    df_inf_pct_a.style
    .format({c: '{:.1f}%' for c in cat_cols if c in df_inf_pct_a.columns} | {'итого $': '${:,.0f}'})
    .background_gradient(cmap="OrRd", axis=None, subset=[c for c in cat_cols if c in df_inf_pct_a.columns])
)
st.dataframe(styled_inf_a, use_container_width=True)

st.markdown("**Абсолютные суммы рефандов ($)**")
styled_inf_amt = (
    df_inf_amt.style
    .format('{:,.0f}')
    .background_gradient(cmap="OrRd", axis=None,
                         subset=[c for c in cat_cols if c in df_inf_amt.columns])
)
st.dataframe(styled_inf_amt, use_container_width=True)

st.divider()

# ── Агентства: доля коннекшенов vs доля рефандеров ───────────────────────────
st.subheader("Агентства: доля коннекшенов vs доля рефандеров — по месяцу регистрации")
st.caption(
    "Коннекшен = уникальная пара id_user–id_trusted_user. "
    "Рефандер = пользователь, у которого топ-девушка по spend за 2 недели до рефанда принадлежит агентству. "
    "Delta = % рефандеров − % коннекшенов."
)


@st.cache_data(ttl=1800)
def load_agency_refund_share():
    client = bigquery.Client(project=PROJECT_ID)
    sql = """
    WITH
    paying AS (
        SELECT id_user, DATE_TRUNC(user_registration, MONTH) AS reg_month
        FROM `asocial-prod.analytics.transactions`
        WHERE status = 'success' AND user_registration >= '2025-01-01'
        GROUP BY 1, 2
    ),
    all_connections AS (
        SELECT p.reg_month, ac.agency,
            COUNT(DISTINCT CONCAT(ac.id_user, ac.id_trusted_user)) AS total_connections,
            COUNT(DISTINCT ac.id_user) AS users_with_connection
        FROM `asocial-prod.analytics.agency_connections` ac
        JOIN paying p ON ac.id_user = p.id_user
        GROUP BY 1, 2
    ),
    all_total AS (
        SELECT reg_month, SUM(total_connections) AS grand_total
        FROM all_connections GROUP BY 1
    ),
    first_refund AS (
        SELECT user_id, MIN(refund_date) AS first_refund_date
        FROM `asocial-prod.analytics.refunds`
        WHERE refund_date IS NOT NULL GROUP BY 1
    ),
    connections_before AS (
        SELECT p.reg_month, ac.id_user, ac.agency,
               SUM(ac.all_spend) AS spend_on_girl
        FROM `asocial-prod.analytics.agency_connections` ac
        JOIN first_refund fr ON ac.id_user = fr.user_id
        JOIN paying p ON ac.id_user = p.id_user
        WHERE ac.date_created >= DATE_SUB(fr.first_refund_date, INTERVAL 14 DAY)
          AND ac.date_created < fr.first_refund_date
        GROUP BY 1, 2, 3
    ),
    top_girl_per_user AS (
        SELECT reg_month, id_user, agency,
               ROW_NUMBER() OVER (PARTITION BY reg_month, id_user ORDER BY spend_on_girl DESC) AS rn
        FROM connections_before
    ),
    refund_by_agency AS (
        SELECT reg_month, agency, COUNT(DISTINCT id_user) AS refund_users
        FROM top_girl_per_user WHERE rn = 1
        GROUP BY 1, 2
    ),
    refund_total AS (
        SELECT reg_month, SUM(refund_users) AS grand_refund
        FROM refund_by_agency GROUP BY 1
    )
    SELECT
        FORMAT_DATE('%Y-%m', ac.reg_month) AS reg_month,
        ac.agency,
        ROUND(ac.total_connections * 100.0 / tot.grand_total, 1) AS pct_connections,
        ROUND(COALESCE(r.refund_users, 0) * 100.0 / rt.grand_refund, 1) AS pct_refunds,
        ROUND(COALESCE(r.refund_users, 0) * 100.0 / rt.grand_refund
            - ac.total_connections * 100.0 / tot.grand_total, 1) AS delta,
        ROUND(COALESCE(r.refund_users, 0) * 100.0 / ac.users_with_connection, 1) AS pct_refund_from_users
    FROM all_connections ac
    JOIN all_total tot ON ac.reg_month = tot.reg_month
    LEFT JOIN refund_by_agency r ON ac.reg_month = r.reg_month AND ac.agency = r.agency
    JOIN refund_total rt ON ac.reg_month = rt.reg_month
    ORDER BY ac.reg_month, pct_connections DESC
    """
    df = client.query(sql).to_dataframe()
    client.close()
    agencies_order = (
        df.groupby('agency')['pct_connections'].mean()
        .sort_values(ascending=False).index.tolist()
    )
    pivot_conn = df.pivot(index='agency', columns='reg_month', values='pct_connections')
    pivot_conn = pivot_conn.reindex(agencies_order).dropna(how='all')
    pivot_ref = df.pivot(index='agency', columns='reg_month', values='pct_refunds')
    pivot_ref = pivot_ref.reindex(agencies_order).dropna(how='all')
    pivot_delta = df.pivot(index='agency', columns='reg_month', values='delta')
    pivot_delta = pivot_delta.reindex(agencies_order).dropna(how='all')
    pivot_conv = df.pivot(index='agency', columns='reg_month', values='pct_refund_from_users')
    pivot_conv = pivot_conv.reindex(agencies_order).dropna(how='all')
    return pivot_conn, pivot_ref, pivot_delta, pivot_conv


with st.spinner("Загружаю данные агентств..."):
    df_ag_conn, df_ag_ref, df_ag_delta, df_ag_conv = load_agency_refund_share()

fmt = {c: '{:.1f}%' for c in df_ag_conn.columns}

st.markdown("**% рефандеров от платников с коннекшеном к агентству**")
st.caption("Числитель: юзеры, чья топ-девушка по spend за 14 дней до рефанда — из агентства. Знаменатель: все платники с хотя бы одним коннекшеном к агентству.")
st.dataframe(
    df_ag_conv.style.format(fmt, na_rep='—')
    .background_gradient(cmap="OrRd", axis=None),
    use_container_width=True
)

st.markdown("**% коннекшенов по агентствам**")
st.dataframe(
    df_ag_conn.style.format(fmt, na_rep='—')
    .background_gradient(cmap="Blues", axis=None),
    use_container_width=True
)

st.markdown("**% рефандеров от всех рефандеров — по агентствам**")
st.dataframe(
    df_ag_ref.style.format(fmt, na_rep='—')
    .background_gradient(cmap="OrRd", axis=None),
    use_container_width=True
)

st.markdown("**Delta (% рефандеров − % коннекшенов)**")
st.dataframe(
    df_ag_delta.style.format(fmt, na_rep='—')
    .background_gradient(cmap="RdYlGn_r", axis=None, vmin=-10, vmax=10),
    use_container_width=True
)

