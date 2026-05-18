import streamlit as st
import pandas as pd
from google.cloud import bigquery

st.set_page_config(page_title="До подписки", layout="wide")
st.title("До подписки")
st.caption("Пользователи, чей первый рефанд был до первой подписки, или в пределах 21 дня с первого платежа (без подписки).")

PROJECT_ID = 'asocial-prod'


@st.cache_data(ttl=1800)
def load_do_podpiski_buckets():
    client = bigquery.Client(project=PROJECT_ID)
    sql = """
    WITH paying AS (
        SELECT id_user, DATE_TRUNC(user_registration, MONTH) AS reg_month
        FROM `asocial-prod.analytics.transactions`
        WHERE status = 'success' AND user_registration >= '2025-01-01'
        GROUP BY 1, 2
    ),
    subs_success AS (
        SELECT id_user, DATE(date_created) AS sub_date
        FROM `asocial-prod.analytics.transactions`
        WHERE transaction_type = 'subscription' AND status = 'success'
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
    nearest_sub AS (
        SELECT fr.user_id, MAX(s.sub_date) AS last_sub_before
        FROM first_refund fr
        JOIN subs_success s ON fr.user_id = s.id_user AND s.sub_date <= fr.first_refund_date
        GROUP BY 1
    ),
    first_sub AS (
        SELECT id_user, MIN(sub_date) AS first_sub_date FROM subs_success GROUP BY 1
    ),
    classified AS (
        SELECT p.id_user, p.reg_month,
            CASE
                WHEN fs.id_user IS NOT NULL THEN
                    CASE
                        WHEN fr.first_refund_date < fs.first_sub_date THEN 'до подписки'
                        WHEN ns.last_sub_before IS NULL THEN 'до подписки'
                        WHEN DATE_DIFF(fr.first_refund_date, ns.last_sub_before, DAY) <= 21 THEN 'влияние подписки'
                        ELSE 'после подписки (21+)'
                    END
                ELSE
                    CASE
                        WHEN fr.first_refund_date IS NULL THEN NULL
                        WHEN DATE_DIFF(fr.first_refund_date, fp.first_payment_date, DAY) <= 21 THEN 'до подписки'
                        ELSE 'после подписки (21+)'
                    END
            END AS category
        FROM paying p
        LEFT JOIN first_refund fr ON p.id_user = fr.user_id
        LEFT JOIN first_sub fs ON p.id_user = fs.id_user
        LEFT JOIN nearest_sub ns ON p.id_user = ns.user_id
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
        WHERE c.category = 'до подписки'
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
def load_do_podpiski_summary():
    client = bigquery.Client(project=PROJECT_ID)
    sql = """
    WITH paying AS (
        SELECT id_user, DATE_TRUNC(user_registration, MONTH) AS reg_month
        FROM `asocial-prod.analytics.transactions`
        WHERE status = 'success' AND user_registration >= '2025-01-01'
        GROUP BY 1, 2
    ),
    subs_success AS (
        SELECT id_user, DATE(date_created) AS sub_date
        FROM `asocial-prod.analytics.transactions`
        WHERE transaction_type = 'subscription' AND status = 'success'
          AND user_registration >= '2025-01-01'
    ),
    subs_fail AS (
        SELECT id_user, DATE(date_created) AS sub_date
        FROM `asocial-prod.analytics.transactions`
        WHERE transaction_type = 'subscription' AND status != 'success'
          AND user_registration >= '2025-01-01'
    ),
    first_sub_success AS (
        SELECT id_user, MIN(sub_date) AS first_sub_date FROM subs_success GROUP BY 1
    ),
    first_sub_fail AS (
        SELECT id_user, MIN(sub_date) AS first_fail_date FROM subs_fail GROUP BY 1
    ),
    first_refund AS (
        SELECT user_id, MIN(refund_date) AS first_refund_date
        FROM `asocial-prod.analytics.refunds`
        WHERE refund_date IS NOT NULL GROUP BY 1
    ),
    nearest_success_sub AS (
        SELECT fr.user_id, MAX(s.sub_date) AS last_sub_before_refund
        FROM first_refund fr
        JOIN subs_success s ON fr.user_id = s.id_user AND s.sub_date <= fr.first_refund_date
        GROUP BY 1
    ),
    nearest_fail_sub AS (
        SELECT fr.user_id, MAX(s.sub_date) AS last_fail_before_refund
        FROM first_refund fr
        JOIN subs_fail s ON fr.user_id = s.id_user AND s.sub_date <= fr.first_refund_date
        GROUP BY 1
    ),
    first_payment AS (
        SELECT id_user, MIN(DATE(date_created)) AS first_payment_date
        FROM `asocial-prod.analytics.transactions`
        WHERE status = 'success' AND user_registration >= '2025-01-01'
        GROUP BY 1
    ),
    classified AS (
        SELECT p.reg_month, p.id_user,
            CASE
                WHEN fss.id_user IS NOT NULL THEN
                    CASE
                        WHEN fr.first_refund_date < fss.first_sub_date THEN 'до подписки'
                        WHEN ns.last_sub_before_refund IS NULL THEN 'до подписки'
                        ELSE 'после подписки'
                    END
                WHEN fsf.id_user IS NOT NULL THEN
                    CASE
                        WHEN fr.first_refund_date < fsf.first_fail_date THEN 'до подписки'
                        WHEN nf.last_fail_before_refund IS NULL THEN 'до подписки'
                        ELSE 'после подписки'
                    END
                ELSE
                    CASE
                        WHEN DATE_DIFF(fr.first_refund_date, fp.first_payment_date, DAY) <= 21 THEN 'до подписки'
                        ELSE 'после подписки'
                    END
            END AS category
        FROM paying p
        JOIN first_refund fr ON p.id_user = fr.user_id
        LEFT JOIN first_sub_success fss ON p.id_user = fss.id_user
        LEFT JOIN first_sub_fail fsf ON p.id_user = fsf.id_user
        LEFT JOIN nearest_success_sub ns ON p.id_user = ns.user_id
        LEFT JOIN nearest_fail_sub nf ON p.id_user = nf.user_id
        LEFT JOIN first_payment fp ON p.id_user = fp.id_user
    ),
    refund_totals AS (
        SELECT c.id_user, ROUND(SUM(r.fee_amount_refund), 2) AS total_refund
        FROM classified c
        JOIN `asocial-prod.analytics.refunds` r ON c.id_user = r.user_id
        WHERE c.category = 'до подписки' AND r.refund_date IS NOT NULL
        GROUP BY 1
    ),
    user_paid AS (
        SELECT id_user, MAX(cumulative_fee_amount) AS total_paid
        FROM `asocial-prod.analytics.transactions`
        WHERE user_registration >= '2025-01-01'
        GROUP BY 1
    )
    SELECT
        p.reg_month,
        COUNT(DISTINCT p.id_user) AS paying_users,
        COUNT(DISTINCT CASE WHEN c.category = 'до подписки' THEN p.id_user END) AS do_podpiski,
        ROUND(COUNT(DISTINCT CASE WHEN c.category = 'до подписки' THEN p.id_user END) * 100.0 / COUNT(DISTINCT p.id_user), 2) AS pct,
        ROUND(SUM(CASE WHEN c.category = 'до подписки' THEN rt.total_refund END), 0) AS total_refund,
        ROUND(SUM(CASE WHEN c.category = 'до подписки' THEN rt.total_refund END) /
              NULLIF(COUNT(DISTINCT CASE WHEN c.category = 'до подписки' THEN p.id_user END), 0), 2) AS avg_refund_per_refunder,
        ROUND(SUM(CASE WHEN c.category = 'до подписки' THEN rt.total_refund END) /
              NULLIF(COUNT(DISTINCT p.id_user), 0), 2) AS avg_refund_per_payer,
        COUNT(DISTINCT CASE WHEN up.total_paid > 1000 THEN p.id_user END) AS whales,
        COUNT(DISTINCT CASE WHEN up.total_paid > 1000 AND c.category = 'до подписки' THEN p.id_user END) AS whales_refunded,
        ROUND(COUNT(DISTINCT CASE WHEN up.total_paid > 1000 AND c.category = 'до подписки' THEN p.id_user END) * 100.0 /
              NULLIF(COUNT(DISTINCT CASE WHEN up.total_paid > 1000 THEN p.id_user END), 0), 2) AS whales_refunded_pct
    FROM paying p
    LEFT JOIN classified c ON p.id_user = c.id_user
    LEFT JOIN refund_totals rt ON p.id_user = rt.id_user
    LEFT JOIN user_paid up ON p.id_user = up.id_user
    GROUP BY 1 ORDER BY 1
    """
    df = client.query(sql).to_dataframe()
    client.close()
    df['reg_month'] = pd.to_datetime(df['reg_month']).dt.strftime('%Y-%m')
    return df


@st.cache_data(ttl=1800)
def load_do_podpiski_by_category():
    client = bigquery.Client(project=PROJECT_ID)
    sql = """
    WITH funnel AS (
        SELECT user_id, category, month_created AS reg_month
        FROM `asocial-prod.analytics.funnel_regular_users`
        WHERE day_after_reg = 'today' AND category IS NOT NULL
    ),
    paying AS (
        SELECT id_user, DATE_TRUNC(user_registration, MONTH) AS reg_month
        FROM `asocial-prod.analytics.transactions`
        WHERE status = 'success' AND user_registration >= '2025-01-01'
        GROUP BY 1, 2
    ),
    subs_success AS (
        SELECT id_user, DATE(date_created) AS sub_date
        FROM `asocial-prod.analytics.transactions`
        WHERE transaction_type = 'subscription' AND status = 'success'
          AND user_registration >= '2025-01-01'
    ),
    subs_fail AS (
        SELECT id_user, DATE(date_created) AS sub_date
        FROM `asocial-prod.analytics.transactions`
        WHERE transaction_type = 'subscription' AND status != 'success'
          AND user_registration >= '2025-01-01'
    ),
    first_sub_success AS (
        SELECT id_user, MIN(sub_date) AS first_sub_date FROM subs_success GROUP BY 1
    ),
    first_sub_fail AS (
        SELECT id_user, MIN(sub_date) AS first_fail_date FROM subs_fail GROUP BY 1
    ),
    first_refund AS (
        SELECT user_id, MIN(refund_date) AS first_refund_date
        FROM `asocial-prod.analytics.refunds`
        WHERE refund_date IS NOT NULL GROUP BY 1
    ),
    nearest_success_sub AS (
        SELECT fr.user_id, MAX(s.sub_date) AS last_sub_before_refund
        FROM first_refund fr
        JOIN subs_success s ON fr.user_id = s.id_user AND s.sub_date <= fr.first_refund_date
        GROUP BY 1
    ),
    nearest_fail_sub AS (
        SELECT fr.user_id, MAX(s.sub_date) AS last_fail_before_refund
        FROM first_refund fr
        JOIN subs_fail s ON fr.user_id = s.id_user AND s.sub_date <= fr.first_refund_date
        GROUP BY 1
    ),
    first_payment AS (
        SELECT id_user, MIN(DATE(date_created)) AS first_payment_date
        FROM `asocial-prod.analytics.transactions`
        WHERE status = 'success' AND user_registration >= '2025-01-01'
        GROUP BY 1
    ),
    classified AS (
        SELECT p.id_user, p.reg_month,
            CASE
                WHEN fr.first_refund_date IS NULL THEN NULL
                WHEN fss.id_user IS NOT NULL THEN
                    CASE
                        WHEN fr.first_refund_date < fss.first_sub_date THEN 'до подписки'
                        WHEN ns.last_sub_before_refund IS NULL THEN 'до подписки'
                        ELSE 'другое'
                    END
                WHEN fsf.id_user IS NOT NULL THEN
                    CASE
                        WHEN fr.first_refund_date < fsf.first_fail_date THEN 'до подписки'
                        WHEN nf.last_fail_before_refund IS NULL THEN 'до подписки'
                        ELSE 'другое'
                    END
                ELSE
                    CASE
                        WHEN DATE_DIFF(fr.first_refund_date, fp.first_payment_date, DAY) <= 21 THEN 'до подписки'
                        ELSE 'другое'
                    END
            END AS category
        FROM paying p
        LEFT JOIN first_refund fr ON p.id_user = fr.user_id
        LEFT JOIN first_sub_success fss ON p.id_user = fss.id_user
        LEFT JOIN first_sub_fail fsf ON p.id_user = fsf.id_user
        LEFT JOIN nearest_success_sub ns ON p.id_user = ns.user_id
        LEFT JOIN nearest_fail_sub nf ON p.id_user = nf.user_id
        LEFT JOIN first_payment fp ON p.id_user = fp.id_user
    ),
    refund_totals AS (
        SELECT user_id, ROUND(SUM(fee_amount_refund), 2) AS total_refund
        FROM `asocial-prod.analytics.refunds`
        WHERE refund_date IS NOT NULL
        GROUP BY 1
    ),
    user_paid AS (
        SELECT id_user, MAX(cumulative_fee_amount) AS total_paid
        FROM `asocial-prod.analytics.transactions`
        WHERE user_registration >= '2025-01-01'
        GROUP BY 1
    ),
    base AS (
        SELECT
            CASE WHEN p.reg_month < '2025-01-01' THEN DATE '2024-01-01' ELSE p.reg_month END AS reg_month,
            f.category,
            p.id_user,
            CASE WHEN c.category = 'до подписки' THEN p.id_user END AS do_podpiski_user,
            CASE WHEN c.category = 'до подписки' THEN rt.total_refund END AS do_podpiski_refund,
            CASE WHEN c.category = 'до подписки' AND up2.total_paid > 1000 THEN p.id_user END AS big_refunder
        FROM paying p
        JOIN funnel f ON p.id_user = f.user_id AND p.reg_month = f.reg_month
        LEFT JOIN classified c ON p.id_user = c.id_user
        LEFT JOIN refund_totals rt ON p.id_user = rt.user_id
        LEFT JOIN user_paid up2 ON p.id_user = up2.id_user
    )
    SELECT
        reg_month,
        category,
        COUNT(DISTINCT id_user) AS paying_users,
        COUNT(DISTINCT do_podpiski_user) AS do_podpiski_users,
        ROUND(COUNT(DISTINCT do_podpiski_user) * 100.0 / NULLIF(COUNT(DISTINCT id_user), 0), 2) AS pct,
        ROUND(SUM(do_podpiski_refund), 0) AS total_refund,
        COUNT(DISTINCT big_refunder) AS big_refunders
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
        do_podpiski_users=('do_podpiski_users', 'sum'),
        total_refund=('total_refund', 'sum'),
        big_refunders=('big_refunders', 'sum')
    ).reset_index()
    df['pct'] = (df['do_podpiski_users'] / df['paying_users'] * 100).round(2)

    exclude_cats = ['Main', 'Native', 'Bing search']
    big_cats = df.groupby('category')['paying_users'].sum()
    big_cats = big_cats[big_cats >= 100].index.tolist()
    big_cats = [c for c in big_cats if c not in exclude_cats]
    df = df[df['category'].isin(big_cats)]

    pivot_pct = df.pivot(index='category', columns='label', values='pct')
    pivot_users = df.pivot(index='category', columns='label', values='paying_users')
    pivot_refund = df.pivot(index='category', columns='label', values='total_refund').fillna(0).round(0).astype(int)
    pivot_refunders = df.pivot(index='category', columns='label', values='do_podpiski_users').fillna(0).astype(int)
    pivot_big = df.pivot(index='category', columns='label', values='big_refunders').fillna(0).astype(int)
    return pivot_pct, pivot_users, pivot_refund, pivot_refunders, pivot_big


@st.cache_data(ttl=1800)
def load_whale_speed():
    client = bigquery.Client(project=PROJECT_ID)
    sql = """
    WITH paying AS (
        SELECT id_user, DATE_TRUNC(user_registration, MONTH) AS reg_month
        FROM `asocial-prod.analytics.transactions`
        WHERE status = 'success' AND user_registration >= '2024-01-01'
        GROUP BY 1, 2
    ),
    payers_3plus AS (
        SELECT id_user, DATE_TRUNC(user_registration, MONTH) AS reg_month
        FROM `asocial-prod.analytics.transactions`
        WHERE status = 'success' AND user_registration >= '2024-01-01'
        GROUP BY 1, 2
        HAVING COUNT(*) >= 3
    ),
    first_payment AS (
        SELECT id_user, DATE_TRUNC(user_registration, MONTH) AS reg_month,
               MIN(DATE(date_created)) AS first_payment_date
        FROM `asocial-prod.analytics.transactions`
        WHERE status = 'success' AND user_registration >= '2024-01-01'
        GROUP BY 1, 2
    ),
    hit_1000 AS (
        SELECT id_user, MIN(DATE(date_created)) AS hit_date
        FROM `asocial-prod.analytics.transactions`
        WHERE status = 'success' AND cumulative_fee_amount >= 500
          AND user_registration >= '2024-01-01'
        GROUP BY 1
    ),
    days_to_whale AS (
        SELECT fp.reg_month, fp.id_user,
               DATE_DIFF(h.hit_date, fp.first_payment_date, DAY) AS days_to_1000
        FROM first_payment fp
        JOIN hit_1000 h ON fp.id_user = h.id_user
    )
    SELECT
        FORMAT_DATE('%Y-%m', p.reg_month) AS reg_month,
        COUNT(DISTINCT p.id_user) AS paying_users,
        COUNT(DISTINCT p3.id_user) AS payers_3plus,
        COUNT(DISTINCT d.id_user) AS whales,
        ROUND(COUNT(DISTINCT d.id_user) * 100.0 / COUNT(DISTINCT p.id_user), 1) AS pct_became_whale,
        ROUND(COUNTIF(d.days_to_1000 <= 21) * 100.0 / COUNT(DISTINCT p.id_user), 1) AS pct_21d,
        ROUND(COUNTIF(d.days_to_1000 <= 21 AND p3.id_user IS NOT NULL) * 100.0 / NULLIF(COUNT(DISTINCT p3.id_user), 0), 1) AS pct_21d_3plus,
        ROUND(AVG(d.days_to_1000), 1) AS avg_days,
        ROUND(APPROX_QUANTILES(d.days_to_1000, 100)[OFFSET(50)], 1) AS median_days
    FROM paying p
    LEFT JOIN payers_3plus p3 ON p.id_user = p3.id_user AND p.reg_month = p3.reg_month
    LEFT JOIN days_to_whale d ON p.id_user = d.id_user AND p.reg_month = d.reg_month
    GROUP BY 1 ORDER BY 1
    """
    df = client.query(sql).to_dataframe()
    client.close()
    return df.set_index('reg_month')


with st.spinner("Загружаю данные..."):
    df_buckets_u, df_buckets_a = load_do_podpiski_buckets()
    df_summary = load_do_podpiski_summary()
    df_cat_pct, df_cat_users, df_cat_refund, df_cat_refunders, df_cat_big = load_do_podpiski_by_category()
    df_whale_speed = load_whale_speed()

buckets = ['1. до $500', '2. $500–1000', '3. $1000–5000', '4. $5000+']

st.subheader("Скорость набора \\$500 у китов — по месяцу регистрации")
st.caption("Кит = пользователь с cumulative_fee_amount ≥ $500. Дни считаются от первого платежа до достижения $500.")
styled_whale = (
    df_whale_speed.style
    .format({
        'paying_users': '{:.0f}',
        'payers_3plus': '{:.0f}',
        'whales': '{:.0f}',
        'pct_became_whale': '{:.1f}%',
        'pct_21d': '{:.1f}%',
        'pct_21d_3plus': '{:.1f}%',
        'avg_days': '{:.1f}',
        'median_days': '{:.0f}',
    })
    .background_gradient(cmap="RdYlGn_r", axis=0, subset=['avg_days', 'median_days'])
    .background_gradient(cmap="OrRd", axis=0, subset=['pct_became_whale', 'pct_21d', 'pct_21d_3plus'])
)
st.dataframe(styled_whale, use_container_width=True)

st.divider()
st.subheader("Доля пользователей с рефандом до подписки — по месяцу регистрации")
styled_summary = (
    df_summary.set_index('reg_month').style
    .format({
        'paying_users': '{:.0f}',
        'do_podpiski': '{:.0f}',
        'pct': '{:.2f}%',
        'total_refund': '${:,.0f}',
        'avg_refund_per_refunder': '${:,.2f}',
        'avg_refund_per_payer': '${:,.2f}',
        'whales': '{:.0f}',
        'whales_refunded': '{:.0f}',
        'whales_refunded_pct': '{:.2f}%',
    })
    .background_gradient(cmap="OrRd", axis=0, subset=['do_podpiski', 'pct', 'total_refund', 'avg_refund_per_refunder', 'avg_refund_per_payer', 'whales_refunded', 'whales_refunded_pct'])
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

st.divider()
st.subheader("% рефандов до подписки по категории трафика и месяцу регистрации")
st.caption("Только пользователи, чей первый рефанд был до подписки. Категории с менее 100 платящими пользователями скрыты.")
month_cols = [c for c in df_cat_pct.columns if c != 'category']
styled_cat = (
    df_cat_pct.style
    .format("{:.1f}%", na_rep="—", subset=month_cols)
    .background_gradient(cmap="RdYlGn_r", axis=None, vmin=2, vmax=4, subset=month_cols)
)
st.dataframe(styled_cat, use_container_width=True)

st.markdown("**Количество рефандеров до подписки по категории трафика**")
refunders_month_cols = [c for c in df_cat_refunders.columns if c != 'category']
df_cat_refunders_total = df_cat_refunders.copy()
df_cat_refunders_total.loc['Total'] = df_cat_refunders_total.sum()
styled_cat_refunders = (
    df_cat_refunders_total.style
    .format("{:.0f}", na_rep="—", subset=refunders_month_cols)
    .background_gradient(cmap="OrRd", axis=None, subset=refunders_month_cols)
)
st.dataframe(styled_cat_refunders, use_container_width=True)

st.markdown("**Рефандеры до подписки с суммарными тратами выше \\$1,000 по категории трафика**")
big_month_cols = [c for c in df_cat_big.columns if c != 'category']
df_cat_big_total = df_cat_big.copy()
df_cat_big_total.loc['Total'] = df_cat_big_total.sum()
styled_cat_big = (
    df_cat_big_total.style
    .format("{:.0f}", na_rep="—", subset=big_month_cols)
    .background_gradient(cmap="OrRd", axis=None, subset=big_month_cols)
)
st.dataframe(styled_cat_big, use_container_width=True)

st.markdown("**Сумма рефандов до подписки по категории трафика, $**")
refund_month_cols = [c for c in df_cat_refund.columns if c != 'category']
df_cat_refund_total = df_cat_refund.copy()
df_cat_refund_total.loc['Total'] = df_cat_refund_total.sum()
styled_cat_r = (
    df_cat_refund_total.style
    .format("${:,.0f}", na_rep="—", subset=refund_month_cols)
    .background_gradient(cmap="OrRd", axis=None, subset=refund_month_cols)
)
st.dataframe(styled_cat_r, use_container_width=True)

st.markdown("**Число платящих пользователей по категории трафика**")
styled_cat_u = (
    df_cat_users.style
    .format("{:.0f}", na_rep="—", subset=month_cols)
)
st.dataframe(styled_cat_u, use_container_width=True)

st.markdown("**Выводы по месяцам (с января 2025)**")
analysis = [
    ("Январь 2025", "18 чел | \\$1,234 | 2 крупных по \\$230–497 — 59% суммы",
     ":green[Спокойный фоновый месяц.] Двое лидеров (\\$497 и \\$230) определяют **59%** суммы, остальные 16 — мелкие (до \\$114)."),
    ("Февраль 2025", "16 чел | \\$984 | 4 по \\$107–261 — 68% суммы",
     ":green[Похоже на январь, но равномернее.] Четверо по \\$107–261 дают **68%** суммы — нет явного доминирования одного."),
    ("Март 2025", "32 чел | \\$24,242 | 1 кит \\$11.2k + 3 по \\$1.8–4.8k — 86% суммы",
     ":red[Первый тревожный месяц.] Один кит вернул \\$11.2k (46% суммы), ещё трое — \\$1.8–4.8k. Итого 4 человека = **86%** суммы. :red[Резкий скачок.]"),
    ("Апрель 2025", "28 чел | \\$2,134 | 3 умеренных по \\$363–530 — 63% суммы",
     ":green[Возврат к норме.] Трое умеренных (\\$363–530) дают **63%** суммы, остальные 25 — мелкие. Нет явного злоупотребления."),
    ("Май 2025", "28 чел | \\$22,018 | 1 кит \\$18.5k — 84% суммы",
     ":red[Аномальный месяц.] Один пользователь вернул \\$18.5k — **84%** всей суммы. *Единичный крупный кейс,* фон нормальный."),
    ("Июнь 2025", "33 чел | \\$3,229 | 2 по \\$440–754 — 37% суммы",
     ":green[Спокойный месяц.] Двое лидеров (\\$754 и \\$440) дают **37%**, остальные 31 человек распределены равномерно — нет явного кита."),
    ("Июль 2025", "43 чел | \\$3,388 | 3 по \\$407–630 — 45% суммы",
     "Аналогично июню. Трое лидеров (\\$407–630) дают **45%**. Пользователей становится больше (43), суммы распределены. :green[Рост числа без роста сумм.]"),
    ("Август 2025", "49 чел | \\$14,644 | 1 кит \\$7.7k + 2 по \\$1.4–1.6k — 74% суммы",
     "Снова кит: один пользователь вернул \\$7.7k (**53%** суммы). Ещё двое по \\$1.4–1.6k. Итого 3 человека = **74%**. Фон продолжает расти."),
    ("Сентябрь 2025", "56 чел | \\$16,678 | 4 по \\$1.3–3.9k — 54% суммы",
     "Нет одного кита, но 4 крупных по \\$1.3–3.9k = **54%** суммы. Плюс 10+ человек с \\$200–900 — :orange[суммарный фон заметно вырос.]"),
    ("Октябрь 2025", "43 чел | \\$16,732 | 1 кит \\$6.9k + 6 по \\$900–2k — 87% суммы",
     "Один кит (\\$6.9k = **41%**) + шестеро по \\$900–2k — итого 7 человек = **87%** суммы. :orange[Наиболее сконцентрированный месяц.]"),
    ("Ноябрь 2025", "35 чел | \\$4,646 | 3 по \\$616–973 — 55% суммы",
     ":green[Относительно спокойный месяц.] Трое лидеров (\\$616–973) дают **55%**, остальные 32 — умеренные и мелкие."),
    ("Декабрь 2025", "46 чел | \\$7,685 | 3 по \\$826–913 — 34% суммы",
     "Первый месяц без явного кита. Трое лидеров (\\$826–913) дают только **34%** — :orange[суммы впервые распределены более равномерно среди 46 человек.]"),
    ("Январь 2026", "83 чел | \\$34,190 | 3 кита по \\$6.7–7.5k — 63% суммы",
     ":red[Резкий рост] и числа, и суммы. Три кита (\\$6.7–7.5k) дают **63%** (\\$21.5k). Фон — 80 человек с суммарно \\$12.7k — :red[рекорд и начало системного роста.]"),
    ("Февраль 2026", "101 чел | \\$22,950 | 1 кит \\$17.4k — 76% суммы",
     "Снова мегакит: один пользователь вернул \\$17.4k (**76%** суммы). Большая часть остальных 100 — мелкие по \\$1 (*фродовый паттерн*). :orange[Фон растёт, но зашумлён фродом.]"),
    ("Март 2026", "89 чел | \\$10,402 | 3 по \\$1.3–2k — 52% суммы",
     "Нет мегакита. Трое лидеров (\\$1.3–2k) дают **52%**. Много мелких по \\$1 (*фродовый паттерн*). :orange[Фон остаётся высоким — \\$5k от 45+ человек без учёта фрода.]"),
]
for month, stats, comment in analysis:
    st.markdown(f"**{month}** — {stats}\n\n{comment}")
    st.markdown("---")

st.info("**Главный вывод:** на протяжении всего 2025 года доля пользователей с рефандом до подписки стабильна — около **1.4–2%**. Большие суммы в отдельных месяцах (март $24k, май $22k, август–октябрь $14–17k) объясняются исключительно единичными крупными кейсами, а не ростом доли. Реальный системный рост начинается только с **января 2026**: % вырастает до 2.5–3.4%, а число людей — с ~30–50 до 83–101 человека в месяц.")

