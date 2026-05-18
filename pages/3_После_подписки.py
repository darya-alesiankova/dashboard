import streamlit as st
import pandas as pd
from google.cloud import bigquery

st.set_page_config(page_title="После подписки", layout="wide")
st.title("После подписки (21+)")
st.caption("Пользователи, чей первый рефанд был через 21+ день после последней попытки списания подписки (успешной или нет). Для пользователей без подписок — рефанд после 42 дней от первой оплаты.")

PROJECT_ID = 'asocial-prod'


@st.cache_data(ttl=1800)
def load_posle_podpiski_buckets():
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
    last_attempt_before AS (
        SELECT fr.user_id, MAX(s.sub_date) AS last_attempt_before
        FROM first_refund fr
        JOIN all_sub_attempts s ON fr.user_id = s.id_user AND s.sub_date <= fr.first_refund_date
        GROUP BY 1
    ),
    classified AS (
        SELECT p.id_user, p.reg_month,
            CASE
                WHEN fr.first_refund_date IS NULL THEN NULL
                WHEN la.last_attempt_before IS NOT NULL AND DATE_DIFF(fr.first_refund_date, la.last_attempt_before, DAY) <= 21 THEN 'влияние подписки'
                WHEN la.last_attempt_before IS NOT NULL AND DATE_DIFF(fr.first_refund_date, la.last_attempt_before, DAY) > 21 THEN 'после подписки (21+)'
                WHEN la.last_attempt_before IS NULL AND DATE_DIFF(fr.first_refund_date, fp.first_payment_date, DAY) > 42 THEN 'после подписки (21+)'
                ELSE 'до подписки'
            END AS category
        FROM paying p
        LEFT JOIN first_refund fr ON p.id_user = fr.user_id
        LEFT JOIN last_attempt_before la ON p.id_user = la.user_id
        LEFT JOIN first_payment fp ON p.id_user = fp.id_user
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
        WHERE c.category = 'после подписки (21+)'
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
def load_posle_podpiski_summary():
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
    last_attempt_before AS (
        SELECT fr.user_id, MAX(s.sub_date) AS last_attempt_before
        FROM first_refund fr
        JOIN all_sub_attempts s ON fr.user_id = s.id_user AND s.sub_date <= fr.first_refund_date
        GROUP BY 1
    ),
    classified AS (
        SELECT p.id_user, p.reg_month,
            CASE
                WHEN fr.first_refund_date IS NULL THEN NULL
                WHEN la.last_attempt_before IS NOT NULL AND DATE_DIFF(fr.first_refund_date, la.last_attempt_before, DAY) > 21 THEN 'после подписки (21+)'
                WHEN la.last_attempt_before IS NULL AND DATE_DIFF(fr.first_refund_date, fp.first_payment_date, DAY) > 42 THEN 'после подписки (21+)'
                ELSE 'другое'
            END AS category
        FROM paying p
        LEFT JOIN first_refund fr ON p.id_user = fr.user_id
        LEFT JOIN last_attempt_before la ON p.id_user = la.user_id
        LEFT JOIN first_payment fp ON p.id_user = fp.id_user
    ),
    refund_totals AS (
        SELECT c.id_user, ROUND(SUM(r.fee_amount_refund), 2) AS total_refund
        FROM classified c
        JOIN `asocial-prod.analytics.refunds` r ON c.id_user = r.user_id
        WHERE c.category = 'после подписки (21+)' AND r.refund_date IS NOT NULL
        GROUP BY 1
    )
    SELECT
        p.reg_month,
        COUNT(DISTINCT p.id_user) AS paying_users,
        COUNT(DISTINCT CASE WHEN c.category = 'после подписки (21+)' THEN p.id_user END) AS posle_podpiski,
        ROUND(COUNT(DISTINCT CASE WHEN c.category = 'после подписки (21+)' THEN p.id_user END) * 100.0 / COUNT(DISTINCT p.id_user), 2) AS pct,
        ROUND(SUM(CASE WHEN c.category = 'после подписки (21+)' THEN rt.total_refund END), 0) AS total_refund,
        ROUND(SUM(CASE WHEN c.category = 'после подписки (21+)' THEN rt.total_refund END) /
              NULLIF(COUNT(DISTINCT CASE WHEN c.category = 'после подписки (21+)' THEN p.id_user END), 0), 2) AS avg_refund_per_refunder,
        ROUND(SUM(CASE WHEN c.category = 'после подписки (21+)' THEN rt.total_refund END) /
              NULLIF(COUNT(DISTINCT p.id_user), 0), 2) AS avg_refund_per_payer
    FROM paying p
    LEFT JOIN classified c ON p.id_user = c.id_user
    LEFT JOIN refund_totals rt ON p.id_user = rt.id_user
    GROUP BY 1 ORDER BY 1
    """
    df = client.query(sql).to_dataframe()
    client.close()
    df['reg_month'] = pd.to_datetime(df['reg_month']).dt.strftime('%Y-%m')
    return df


with st.spinner("Загружаю данные..."):
    df_buckets_u, df_buckets_a = load_posle_podpiski_buckets()
    df_summary = load_posle_podpiski_summary()

buckets = ['1. до $500', '2. $500–1000', '3. $1000–5000', '4. $5000+']

st.subheader("Доля пользователей с рефандом после подписки — по месяцу регистрации")
styled_summary = (
    df_summary.set_index('reg_month').style
    .format({
        'paying_users': '{:.0f}',
        'posle_podpiski': '{:.0f}',
        'pct': '{:.2f}%',
        'total_refund': '${:,.0f}',
        'avg_refund_per_refunder': '${:,.2f}',
        'avg_refund_per_payer': '${:,.2f}',
    })
    .background_gradient(cmap="OrRd", axis=0, subset=['posle_podpiski', 'pct', 'total_refund', 'avg_refund_per_refunder', 'avg_refund_per_payer'])
)
st.dataframe(styled_summary, use_container_width=True)

st.divider()
st.subheader("Распределение по сумме оплат пользователя — по месяцу регистрации")

st.markdown("**% пользователей по бакетам**")
styled_bu = (
    df_buckets_u.style
    .format({c: '{:.1f}%' for c in buckets} | {'всего чел': '{:.0f}'})
    .background_gradient(cmap="OrRd", axis=None, subset=buckets)
)
st.dataframe(styled_bu, use_container_width=True)

st.markdown("**% суммы рефандов по бакетам**")
styled_ba = (
    df_buckets_a.style
    .format({c: '{:.1f}%' for c in buckets} | {'всего $': '{:,.0f}'})
    .background_gradient(cmap="OrRd", axis=None, subset=buckets)
)
st.dataframe(styled_ba, use_container_width=True)
