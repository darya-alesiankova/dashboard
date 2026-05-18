import streamlit as st
import pandas as pd
from google.cloud import bigquery

st.set_page_config(page_title="Влияние подписки", layout="wide")
st.title("Влияние подписки")
st.caption(
    "Жизненный цикл: зрелые когорты май–июль 2025 (завершённые = факт, активные = 12 списаний). "
    "Рефанды: первый рефанд в пределах 7 дней после любой попытки списания подписки (успешной или нет). "
    "Цена подписки: $16.99."
)

PROJECT_ID = 'asocial-prod'


@st.cache_data(ttl=1800)
def load_pl_by_month():
    client = bigquery.Client(project=PROJECT_ID)
    sql = """
    WITH
    -- Mature cohort lifecycle: May-Jul 2025
    mature_sub_users AS (
        SELECT DISTINCT id_user
        FROM `asocial-prod.analytics.transactions`
        WHERE transaction_type = 'subscription' AND status = 'success'
          AND user_registration >= '2025-05-01' AND user_registration < '2025-08-01'
    ),
    mature_stats AS (
        SELECT
            t.id_user,
            CASE
                WHEN MAX(t.cumulative_fee_amount) < 500 THEN '1. до $500'
                WHEN MAX(t.cumulative_fee_amount) < 1000 THEN '2. $500–1000'
                WHEN MAX(t.cumulative_fee_amount) < 2000 THEN '3a. $1000–2000'
                WHEN MAX(t.cumulative_fee_amount) < 3000 THEN '3b. $2000–3000'
                WHEN MAX(t.cumulative_fee_amount) < 4000 THEN '3c. $3000–4000'
                WHEN MAX(t.cumulative_fee_amount) < 5000 THEN '3d. $4000–5000'
                ELSE '4. $5000+'
            END AS bucket,
            COUNT(DISTINCT CASE WHEN t.transaction_type = 'subscription' AND t.status = 'success'
                                THEN DATE(t.date_created) END) AS actual_charges,
            MAX(CASE WHEN t.transaction_type = 'subscription' AND t.status = 'success'
                     THEN DATE(t.date_created) END) AS last_charge_date
        FROM `asocial-prod.analytics.transactions` t
        WHERE t.id_user IN (SELECT id_user FROM mature_sub_users)
          AND t.user_registration >= '2025-05-01' AND t.user_registration < '2025-08-01'
        GROUP BY 1
    ),
    avg_lifecycle AS (
        SELECT bucket,
            ROUND(AVG(
                CASE WHEN last_charge_date < DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)
                     THEN actual_charges
                     ELSE 12
                END
            ), 2) AS avg_charges
        FROM mature_stats
        GROUP BY 1
    ),
    -- All months: May 2025 – Feb 2026
    target_users AS (
        SELECT DISTINCT id_user, DATE_TRUNC(user_registration, MONTH) AS reg_month
        FROM `asocial-prod.analytics.transactions`
        WHERE user_registration >= '2025-05-01' AND user_registration < '2026-03-01'
        GROUP BY 1, 2
    ),
    -- Sub users per month
    sub_users AS (
        SELECT DISTINCT id_user, DATE_TRUNC(user_registration, MONTH) AS reg_month
        FROM `asocial-prod.analytics.transactions`
        WHERE transaction_type = 'subscription' AND status = 'success'
          AND user_registration >= '2025-05-01' AND user_registration < '2026-03-01'
    ),
    user_buckets AS (
        SELECT
            tu.id_user,
            tu.reg_month,
            CASE
                WHEN MAX(t.cumulative_fee_amount) < 500 THEN '1. до $500'
                WHEN MAX(t.cumulative_fee_amount) < 1000 THEN '2. $500–1000'
                WHEN MAX(t.cumulative_fee_amount) < 2000 THEN '3a. $1000–2000'
                WHEN MAX(t.cumulative_fee_amount) < 3000 THEN '3b. $2000–3000'
                WHEN MAX(t.cumulative_fee_amount) < 4000 THEN '3c. $3000–4000'
                WHEN MAX(t.cumulative_fee_amount) < 5000 THEN '3d. $4000–5000'
                ELSE '4. $5000+'
            END AS bucket
        FROM target_users tu
        JOIN `asocial-prod.analytics.transactions` t ON tu.id_user = t.id_user
        GROUP BY 1, 2
    ),
    sub_counts AS (
        SELECT ub.reg_month, ub.bucket, COUNT(DISTINCT su.id_user) AS sub_users
        FROM user_buckets ub
        LEFT JOIN sub_users su ON ub.id_user = su.id_user AND ub.reg_month = su.reg_month
        GROUP BY 1, 2
    ),
    -- All sub attempts per user (for 7-day window)
    all_sub_attempts AS (
        SELECT id_user, DATE(date_created) AS sub_date
        FROM `asocial-prod.analytics.transactions`
        WHERE transaction_type = 'subscription'
          AND user_registration >= '2025-05-01' AND user_registration < '2026-03-01'
    ),
    first_refund AS (
        SELECT r.user_id, MIN(r.refund_date) AS first_refund_date
        FROM `asocial-prod.analytics.refunds` r
        JOIN target_users tu ON r.user_id = tu.id_user
        WHERE r.refund_date IS NOT NULL
        GROUP BY 1
    ),
    last_attempt_before_refund AS (
        SELECT fr.user_id, MAX(s.sub_date) AS last_attempt_before
        FROM first_refund fr
        JOIN all_sub_attempts s ON fr.user_id = s.id_user AND s.sub_date <= fr.first_refund_date
        GROUP BY 1
    ),
    vliyanie_users AS (
        SELECT fr.user_id
        FROM first_refund fr
        JOIN last_attempt_before_refund la ON fr.user_id = la.user_id
        WHERE DATE_DIFF(fr.first_refund_date, la.last_attempt_before, DAY) <= 7
    ),
    -- Refunds per user (only влияние подписки users)
    refund_per_user AS (
        SELECT ub.reg_month, ub.bucket, r.user_id,
            ROUND(SUM(r.fee_amount_refund), 2) AS user_refund
        FROM `asocial-prod.analytics.refunds` r
        JOIN vliyanie_users vu ON r.user_id = vu.user_id
        JOIN user_buckets ub ON r.user_id = ub.id_user
        GROUP BY 1, 2, 3
    ),
    refund_totals AS (
        SELECT reg_month, bucket,
            ROUND(SUM(user_refund), 0) AS total_refunds,
            COUNT(DISTINCT user_id) AS refund_users
        FROM refund_per_user
        GROUP BY 1, 2
    ),
    -- Users who made 40%+ of total refund amount in their bucket/month
    whales AS (
        SELECT rpu.reg_month, rpu.bucket,
            COUNT(DISTINCT CASE
                WHEN rpu.user_refund >= 0.4 * rt.total_refunds THEN rpu.user_id
            END) AS whale_users
        FROM refund_per_user rpu
        JOIN refund_totals rt ON rpu.reg_month = rt.reg_month AND rpu.bucket = rt.bucket
        GROUP BY 1, 2
    )
    SELECT
        sc.reg_month,
        sc.bucket,
        sc.sub_users,
        al.avg_charges AS expected_charges,
        ROUND(sc.sub_users * al.avg_charges * 16.99, 0) AS sub_revenue,
        COALESCE(rt.total_refunds, 0) AS total_refunds,
        ROUND(sc.sub_users * al.avg_charges * 16.99 - COALESCE(rt.total_refunds, 0), 0) AS netto,
        ROUND((sc.sub_users * al.avg_charges * 16.99 - COALESCE(rt.total_refunds, 0)) / NULLIF(sc.sub_users, 0), 1) AS netto_per_user,
        COALESCE(rt.refund_users, 0) AS refund_users,
        ROUND(COALESCE(rt.refund_users, 0) * 100.0 / NULLIF(sc.sub_users, 0), 1) AS refund_pct,
        COALESCE(w.whale_users, 0) AS whale_users
    FROM sub_counts sc
    JOIN avg_lifecycle al ON sc.bucket = al.bucket
    LEFT JOIN refund_totals rt ON sc.reg_month = rt.reg_month AND sc.bucket = rt.bucket
    LEFT JOIN whales w ON sc.reg_month = w.reg_month AND sc.bucket = w.bucket
    WHERE sc.sub_users > 0
    ORDER BY 1, 2
    """
    df = client.query(sql).to_dataframe()
    client.close()
    df['reg_month'] = pd.to_datetime(df['reg_month']).dt.strftime('%Y-%m')
    return df


def render_month_table(df_month, month_label):
    df = df_month.copy()
    df = df.drop(columns=['reg_month'])
    df.columns = ['Группа', 'Подписчиков', 'Ср. списаний', 'Sub выручка $', 'Рефанды $', 'Нетто $', 'Нетто / юзер $', 'Рефандили', '% рефандили', 'Киты 40%+']

    totals = {
        'Группа': 'ИТОГО',
        'Подписчиков': int(df['Подписчиков'].sum()),
        'Ср. списаний': '',
        'Sub выручка $': int(df['Sub выручка $'].sum()),
        'Рефанды $': int(df['Рефанды $'].sum()),
        'Нетто $': int(df['Нетто $'].sum()),
        'Нетто / юзер $': '',
        'Рефандили': int(df['Рефандили'].sum()),
        '% рефандили': '',
        'Киты 40%+': int(df['Киты 40%+'].sum()),
    }
    df = pd.concat([df, pd.DataFrame([totals])], ignore_index=True)

    def color_netto(val):
        if val == '':
            return ''
        try:
            v = float(val)
            color = '#d4edda' if v > 0 else '#f8d7da'
            return f'background-color: {color}; color: black'
        except:
            return ''

    styled = (
        df.set_index('Группа').style
        .format({
            'Подписчиков': '{:.0f}',
            'Ср. списаний': lambda x: f'{x:.2f}' if x != '' else '',
            'Sub выручка $': lambda x: f'${x:,.0f}' if x != '' else '',
            'Рефанды $': lambda x: f'${x:,.0f}' if x != '' else '',
            'Нетто $': lambda x: f'${x:,.0f}' if x != '' else '',
            'Нетто / юзер $': lambda x: f'${x:.1f}' if x != '' else '',
            'Рефандили': '{:.0f}',
            '% рефандили': lambda x: f'{x:.1f}%' if x != '' else '',
            'Киты 40%+': '{:.0f}',
        })
        .applymap(color_netto, subset=['Нетто $', 'Нетто / юзер $'])
    )
    st.dataframe(styled, use_container_width=True)


@st.cache_data(ttl=1800)
def load_vliyanie_buckets():
    client = bigquery.Client(project='asocial-prod')
    sql = """
    WITH paying AS (
        SELECT id_user, DATE_TRUNC(user_registration, MONTH) AS reg_month
        FROM `asocial-prod.analytics.transactions`
        WHERE status = 'success' AND user_registration >= '2025-01-01'
        GROUP BY 1, 2
    ),
    -- All sub attempts (success + fail)
    all_sub_attempts AS (
        SELECT id_user, DATE(date_created) AS sub_date
        FROM `asocial-prod.analytics.transactions`
        WHERE transaction_type = 'subscription'
          AND user_registration >= '2025-01-01'
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
                WHEN la.last_attempt_before IS NOT NULL
                     AND DATE_DIFF(fr.first_refund_date, la.last_attempt_before, DAY) <= 7
                THEN 'влияние подписки'
                ELSE NULL
            END AS category
        FROM paying p
        LEFT JOIN first_refund fr ON p.id_user = fr.user_id
        LEFT JOIN last_attempt_before_refund la ON p.id_user = la.user_id
    ),
    user_paid AS (
        SELECT id_user, MAX(cumulative_fee_amount) AS total_paid
        FROM `asocial-prod.analytics.transactions`
        WHERE user_registration >= '2025-01-01'
        GROUP BY 1
    ),
    user_refund_totals AS (
        SELECT user_id, ROUND(SUM(fee_amount_refund), 2) AS total_refund
        FROM `asocial-prod.analytics.refunds`
        WHERE refund_date IS NOT NULL
        GROUP BY 1
    ),
    base AS (
        SELECT c.reg_month,
            CASE
                WHEN up.total_paid < 500 THEN '1. до $500'
                WHEN up.total_paid < 1000 THEN '2. $500–1000'
                WHEN up.total_paid < 5000 THEN '3. $1000–5000'
                ELSE '4. $5000+'
            END AS bucket,
            c.id_user, rt.total_refund
        FROM classified c
        JOIN user_paid up ON c.id_user = up.id_user
        JOIN user_refund_totals rt ON c.id_user = rt.user_id
        WHERE c.category = 'влияние подписки'
    )
    SELECT reg_month, bucket,
        COUNT(DISTINCT id_user) AS users,
        ROUND(COUNT(DISTINCT id_user) * 100.0 / SUM(COUNT(DISTINCT id_user)) OVER (PARTITION BY reg_month), 1) AS pct_users,
        ROUND(SUM(total_refund), 0) AS refund_amount,
        ROUND(SUM(total_refund) * 100.0 / SUM(SUM(total_refund)) OVER (PARTITION BY reg_month), 1) AS pct_amount
    FROM base
    GROUP BY 1, 2 ORDER BY 1, 2
    """
    df = client.query(sql).to_dataframe()
    client.close()
    df['reg_month'] = pd.to_datetime(df['reg_month']).dt.strftime('%Y-%m')
    buckets = ['1. до $500', '2. $500–1000', '3. $1000–5000', '4. $5000+']
    pivot_u = df.pivot(index='reg_month', columns='bucket', values='pct_users').fillna(0).round(1)
    pivot_u = pivot_u[[c for c in buckets if c in pivot_u.columns]]
    pivot_u['всего чел'] = df.groupby('reg_month')['users'].sum().astype(int)
    pivot_a = df.pivot(index='reg_month', columns='bucket', values='pct_amount').fillna(0).round(1)
    pivot_a = pivot_a[[c for c in buckets if c in pivot_a.columns]]
    pivot_a['всего $'] = df.groupby('reg_month')['refund_amount'].sum().astype(int)
    return pivot_u, pivot_a


@st.cache_data(ttl=1800)
def load_pl_by_month_14():
    client = bigquery.Client(project=PROJECT_ID)
    sql = """
    WITH
    mature_sub_users AS (
        SELECT DISTINCT id_user
        FROM `asocial-prod.analytics.transactions`
        WHERE transaction_type = 'subscription' AND status = 'success'
          AND user_registration >= '2025-05-01' AND user_registration < '2025-08-01'
    ),
    mature_stats AS (
        SELECT t.id_user,
            CASE
                WHEN MAX(t.cumulative_fee_amount) < 500 THEN '1. до $500'
                WHEN MAX(t.cumulative_fee_amount) < 1000 THEN '2. $500–1000'
                WHEN MAX(t.cumulative_fee_amount) < 2000 THEN '3a. $1000–2000'
                WHEN MAX(t.cumulative_fee_amount) < 3000 THEN '3b. $2000–3000'
                WHEN MAX(t.cumulative_fee_amount) < 4000 THEN '3c. $3000–4000'
                WHEN MAX(t.cumulative_fee_amount) < 5000 THEN '3d. $4000–5000'
                ELSE '4. $5000+'
            END AS bucket,
            COUNT(DISTINCT CASE WHEN t.transaction_type = 'subscription' AND t.status = 'success'
                                THEN DATE(t.date_created) END) AS actual_charges,
            MAX(CASE WHEN t.transaction_type = 'subscription' AND t.status = 'success'
                     THEN DATE(t.date_created) END) AS last_charge_date
        FROM `asocial-prod.analytics.transactions` t
        WHERE t.id_user IN (SELECT id_user FROM mature_sub_users)
          AND t.user_registration >= '2025-05-01' AND t.user_registration < '2025-08-01'
        GROUP BY 1
    ),
    avg_lifecycle AS (
        SELECT bucket,
            ROUND(AVG(CASE WHEN last_charge_date < DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)
                           THEN actual_charges ELSE 12 END), 2) AS avg_charges
        FROM mature_stats GROUP BY 1
    ),
    target_users AS (
        SELECT DISTINCT id_user, DATE_TRUNC(user_registration, MONTH) AS reg_month
        FROM `asocial-prod.analytics.transactions`
        WHERE user_registration >= '2025-05-01' AND user_registration < '2026-03-01'
        GROUP BY 1, 2
    ),
    sub_users AS (
        SELECT DISTINCT id_user, DATE_TRUNC(user_registration, MONTH) AS reg_month
        FROM `asocial-prod.analytics.transactions`
        WHERE transaction_type = 'subscription' AND status = 'success'
          AND user_registration >= '2025-05-01' AND user_registration < '2026-03-01'
    ),
    user_buckets AS (
        SELECT tu.id_user, tu.reg_month,
            CASE
                WHEN MAX(t.cumulative_fee_amount) < 500 THEN '1. до $500'
                WHEN MAX(t.cumulative_fee_amount) < 1000 THEN '2. $500–1000'
                WHEN MAX(t.cumulative_fee_amount) < 2000 THEN '3a. $1000–2000'
                WHEN MAX(t.cumulative_fee_amount) < 3000 THEN '3b. $2000–3000'
                WHEN MAX(t.cumulative_fee_amount) < 4000 THEN '3c. $3000–4000'
                WHEN MAX(t.cumulative_fee_amount) < 5000 THEN '3d. $4000–5000'
                ELSE '4. $5000+'
            END AS bucket
        FROM target_users tu
        JOIN `asocial-prod.analytics.transactions` t ON tu.id_user = t.id_user
        GROUP BY 1, 2
    ),
    sub_counts AS (
        SELECT ub.reg_month, ub.bucket, COUNT(DISTINCT su.id_user) AS sub_users
        FROM user_buckets ub
        LEFT JOIN sub_users su ON ub.id_user = su.id_user AND ub.reg_month = su.reg_month
        GROUP BY 1, 2
    ),
    all_sub_attempts AS (
        SELECT id_user, DATE(date_created) AS sub_date
        FROM `asocial-prod.analytics.transactions`
        WHERE transaction_type = 'subscription'
          AND user_registration >= '2025-05-01' AND user_registration < '2026-03-01'
    ),
    first_refund AS (
        SELECT r.user_id, MIN(r.refund_date) AS first_refund_date
        FROM `asocial-prod.analytics.refunds` r
        JOIN target_users tu ON r.user_id = tu.id_user
        WHERE r.refund_date IS NOT NULL GROUP BY 1
    ),
    last_attempt_before_refund AS (
        SELECT fr.user_id, MAX(s.sub_date) AS last_attempt_before
        FROM first_refund fr
        JOIN all_sub_attempts s ON fr.user_id = s.id_user AND s.sub_date <= fr.first_refund_date
        GROUP BY 1
    ),
    vliyanie_users AS (
        SELECT fr.user_id FROM first_refund fr
        JOIN last_attempt_before_refund la ON fr.user_id = la.user_id
        WHERE DATE_DIFF(fr.first_refund_date, la.last_attempt_before, DAY) <= 14
    ),
    refund_per_user AS (
        SELECT ub.reg_month, ub.bucket, r.user_id,
            ROUND(SUM(r.fee_amount_refund), 2) AS user_refund
        FROM `asocial-prod.analytics.refunds` r
        JOIN vliyanie_users vu ON r.user_id = vu.user_id
        JOIN user_buckets ub ON r.user_id = ub.id_user
        GROUP BY 1, 2, 3
    ),
    refund_totals AS (
        SELECT reg_month, bucket,
            ROUND(SUM(user_refund), 0) AS total_refunds,
            COUNT(DISTINCT user_id) AS refund_users
        FROM refund_per_user GROUP BY 1, 2
    ),
    whales AS (
        SELECT rpu.reg_month, rpu.bucket,
            COUNT(DISTINCT CASE WHEN rpu.user_refund >= 0.4 * rt.total_refunds THEN rpu.user_id END) AS whale_users
        FROM refund_per_user rpu
        JOIN refund_totals rt ON rpu.reg_month = rt.reg_month AND rpu.bucket = rt.bucket
        GROUP BY 1, 2
    )
    SELECT sc.reg_month, sc.bucket, sc.sub_users,
        al.avg_charges AS expected_charges,
        ROUND(sc.sub_users * al.avg_charges * 16.99, 0) AS sub_revenue,
        COALESCE(rt.total_refunds, 0) AS total_refunds,
        ROUND(sc.sub_users * al.avg_charges * 16.99 - COALESCE(rt.total_refunds, 0), 0) AS netto,
        ROUND((sc.sub_users * al.avg_charges * 16.99 - COALESCE(rt.total_refunds, 0)) / NULLIF(sc.sub_users, 0), 1) AS netto_per_user,
        COALESCE(rt.refund_users, 0) AS refund_users,
        ROUND(COALESCE(rt.refund_users, 0) * 100.0 / NULLIF(sc.sub_users, 0), 1) AS refund_pct,
        COALESCE(w.whale_users, 0) AS whale_users
    FROM sub_counts sc
    JOIN avg_lifecycle al ON sc.bucket = al.bucket
    LEFT JOIN refund_totals rt ON sc.reg_month = rt.reg_month AND sc.bucket = rt.bucket
    LEFT JOIN whales w ON sc.reg_month = w.reg_month AND sc.bucket = w.bucket
    WHERE sc.sub_users > 0
    ORDER BY 1, 2
    """
    df = client.query(sql).to_dataframe()
    client.close()
    df['reg_month'] = pd.to_datetime(df['reg_month']).dt.strftime('%Y-%m')
    return df


with st.spinner("Загружаю данные..."):
    df_all = load_pl_by_month()
    df_all_14 = load_pl_by_month_14()
    df_vl_buckets_u, df_vl_buckets_a = load_vliyanie_buckets()

months = sorted(df_all['reg_month'].unique())

month_labels = {
    '2025-05': 'Май 2025',
    '2025-06': 'Июнь 2025',
    '2025-07': 'Июль 2025',
    '2025-08': 'Август 2025',
    '2025-09': 'Сентябрь 2025',
    '2025-10': 'Октябрь 2025',
    '2025-11': 'Ноябрь 2025',
    '2025-12': 'Декабрь 2025',
    '2026-01': 'Январь 2026',
    '2026-02': 'Февраль 2026',
}

st.subheader("Распределение по сумме оплат пользователя — по месяцу регистрации")
st.markdown("**% пользователей по бакетам**")
buckets = ['1. до $500', '2. $500–1000', '3. $1000–5000', '4. $5000+']
styled_vl_bu = (
    df_vl_buckets_u.style
    .format({c: '{:.1f}%' for c in buckets} | {'всего чел': '{:.0f}'})
    .background_gradient(cmap="OrRd", axis=None, subset=buckets)
)
st.dataframe(styled_vl_bu, use_container_width=True)

st.markdown("**% суммы рефандов по бакетам**")
styled_vl_ba = (
    df_vl_buckets_a.style
    .format({c: '{:.1f}%' for c in buckets} | {'всего $': '{:,.0f}'})
    .background_gradient(cmap="OrRd", axis=None, subset=buckets)
)
st.dataframe(styled_vl_ba, use_container_width=True)

st.divider()
st.subheader("P&L по месяцу регистрации")
st.caption(
    "Жизненный цикл: зрелые когорты май–июль 2025 (завершённые = факт, активные = 12 списаний). "
    "Рефанды: первый рефанд в пределах 7 дней после любой попытки списания подписки. "
    "Цена подписки: $16.99."
)

for month in months:
    label = month_labels.get(month, month)
    st.subheader(label)
    df_month = df_all[df_all['reg_month'] == month]
    render_month_table(df_month, label)
    st.markdown("")

_BUCKET_ORDER = ['1. до $500', '2. $500–1000', '3a. $1000–2000', '3b. $2000–3000', '3c. $3000–4000', '3d. $4000–5000', '4. $5000+']

def make_total_table(df_src):
    df_t = df_src.groupby('bucket', sort=False).agg(
        sub_users=('sub_users', 'sum'),
        expected_charges=('expected_charges', 'first'),
        sub_revenue=('sub_revenue', 'sum'),
        total_refunds=('total_refunds', 'sum'),
        netto=('netto', 'sum'),
        refund_users=('refund_users', 'sum'),
        whale_users=('whale_users', 'sum'),
    ).reset_index()
    df_t['netto_per_user'] = (df_t['netto'] / df_t['sub_users']).round(1)
    df_t['refund_pct'] = (df_t['refund_users'] / df_t['sub_users'] * 100).round(1)
    df_t['bucket'] = pd.Categorical(df_t['bucket'], categories=_BUCKET_ORDER, ordered=True)
    df_t = df_t.sort_values('bucket')
    df_t.insert(0, 'reg_month', 'итого')
    return df_t[['reg_month', 'bucket', 'sub_users', 'expected_charges',
                 'sub_revenue', 'total_refunds', 'netto', 'netto_per_user',
                 'refund_users', 'refund_pct', 'whale_users']]


st.divider()
st.subheader("Итого с мая 2025 — окно 0–7 дней")
render_month_table(make_total_table(df_all), 'Итого 0–7')

st.subheader("Итого с мая 2025 — окно 0–14 дней")
render_month_table(make_total_table(df_all_14), 'Итого 0–14')
