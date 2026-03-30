import streamlit as st
import pandas as pd
from supabase import create_client, Client
import plotly.express as px
import plotly.graph_objects as go
import os
import hashlib

# Load environment variables (works locally with .env file)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

st.set_page_config(page_title="CloudAI 2026 Summary", layout="wide")

# ── Supabase connection ──────────────────────────────────────────────────────

def init_supabase():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        try:
            url = st.secrets["SUPABASE_URL"]
            key = st.secrets["SUPABASE_KEY"]
        except:
            pass
    if not url or not key:
        st.error("Please set SUPABASE_URL and SUPABASE_KEY in .env file or Streamlit secrets")
        st.stop()
    return create_client(url, key)

supabase = init_supabase()

# ── Authentication helpers ───────────────────────────────────────────────────

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

@st.cache_data(ttl=60)
def get_distinct_cooperatives():
    """Pull distinct cooperative names from the farmers table."""
    try:
        result = supabase.table('farmers').select('cooperative').execute()
        coops = sorted(set(
            row['cooperative'] for row in result.data
            if row.get('cooperative')
        ))
        return coops
    except Exception as e:
        st.error(f"Could not load cooperatives: {e}")
        return []

def authenticate(username: str, password: str):
    """
    Returns (success: bool, user_row: dict | None).
    Accepts both plain-text and SHA-256-hashed passwords stored in the users table.
    Expected columns: username, password, cooperative, role
    """
    try:
        result = supabase.table('users').select('*').eq('username', username).execute()
    except Exception as e:
        st.error(f"Could not query users table: {e}")
        return False, None

    if not result.data:
        return False, None

    user = result.data[0]
    stored_pw = user.get('password', '')

    # Accept plain-text OR hashed password
    if stored_pw == password or stored_pw == hash_password(password):
        return True, user

    return False, None

def update_password(username: str, new_password: str):
    """Store hashed password back to users table."""
    try:
        supabase.table('users').update(
            {'password': hash_password(new_password)}
        ).eq('username', username).execute()
        return True
    except Exception as e:
        st.error(f"Failed to update password: {e}")
        return False

# ── Login page ───────────────────────────────────────────────────────────────

def show_login():
    st.title("🔐 Login")
    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Login")

    if submitted:
        if not username or not password:
            st.warning("Please enter both username and password.")
            return
        success, user = authenticate(username, password)
        if success:
            st.session_state['authenticated'] = True
            st.session_state['user'] = user
            st.rerun()
        else:
            st.error("Invalid username or password.")

# ── Session state init ───────────────────────────────────────────────────────

if 'authenticated' not in st.session_state:
    st.session_state['authenticated'] = False
if 'user' not in st.session_state:
    st.session_state['user'] = None

if not st.session_state['authenticated']:
    show_login()
    st.stop()

# ── From here on: user is authenticated ─────────────────────────────────────

current_user = st.session_state['user']
current_role = current_user.get('role', 'coop')          # 'admin' or 'coop'
current_coop = current_user.get('cooperative', '')        # cooperative name for coop users

# ── Sidebar: user info + password change + logout ────────────────────────────

with st.sidebar:
    st.markdown(f"**Logged in as:** {current_user.get('username')}")
    if current_role == 'admin':
        st.caption("Role: Admin (all cooperatives)")
    else:
        st.caption(f"Role: Cooperative — {current_coop}")

    st.divider()

    with st.expander("🔑 Change Password"):
        with st.form("change_pw_form"):
            new_pw = st.text_input("New password", type="password")
            confirm_pw = st.text_input("Confirm new password", type="password")
            change_submitted = st.form_submit_button("Update password")
        if change_submitted:
            if not new_pw:
                st.warning("Password cannot be empty.")
            elif new_pw != confirm_pw:
                st.error("Passwords do not match.")
            else:
                if update_password(current_user.get('username'), new_pw):
                    st.success("Password updated successfully.")

    st.divider()
    if st.button("Logout"):
        st.session_state['authenticated'] = False
        st.session_state['user'] = None
        st.rerun()

# ── Load data ────────────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def load_data():
    def load_batched(table_name, page_size=1000):
        offset = 0
        all_rows = []
        try:
            while True:
                result = supabase.table(table_name).select('*').range(offset, offset + page_size - 1).execute()
                rows = result.data
                if rows is None:
                    st.error(f"Failed to fetch data from '{table_name}' — no data returned.")
                    return pd.DataFrame()
                if not rows:
                    break
                all_rows.extend(rows)
                offset += page_size
            return pd.DataFrame(all_rows)
        except Exception as e:
            st.error(f"Error loading '{table_name}': {e}")
            return pd.DataFrame()

    st.info("Loading farmers data...")
    farmers_df = load_batched('farmers')
    st.info("Loading traceability data...")
    trace_df = load_batched('traceability')
    st.success(f"✓ Loaded {len(farmers_df)} farmers and {len(trace_df)} traceability records")
    return farmers_df, trace_df

try:
    farmers_df, trace_df = load_data()

    # Standardize farmer_id
    farmers_df['farmer_id'] = farmers_df['farmer_id'].astype(str).str.strip().str.lower()
    trace_df['farmer_id'] = trace_df['farmer_id'].astype(str).str.strip().str.lower()

    # Aggregate traceability per farmer
    trace_agg = trace_df.groupby('farmer_id').agg({
        'net_weight_kg': 'sum',
        'certification': lambda x: ', '.join([str(v) for v in x.dropna().unique() if str(v).strip()]),
        'exporter': 'first'
    }).reset_index()
    trace_agg['certification'] = trace_agg['certification'].replace('', 'Unknown')
    trace_agg['exporter'] = trace_agg['exporter'].fillna('Unknown')

    # Merge
    merged_df = farmers_df.merge(trace_agg, on='farmer_id', how='left')
    merged_df['net_weight_kg'] = merged_df['net_weight_kg'].fillna(0)
    merged_df['certification'] = merged_df['certification'].fillna('Unknown')
    merged_df['exporter'] = merged_df['exporter'].fillna('Unknown')
    merged_df['delivery_percentage'] = (
        merged_df['net_weight_kg'] / merged_df['max_quota_kg'] * 100
    ).round(2).fillna(0)

    # ── Restrict data for coop users ─────────────────────────────────────────
    if current_role != 'admin':
        merged_df = merged_df[merged_df['cooperative'] == current_coop]

    # ── Sidebar filters ───────────────────────────────────────────────────────
    st.sidebar.header("Filters")

    if current_role == 'admin':
        cooperatives = ['All'] + sorted(merged_df['cooperative'].unique().tolist())
        selected_coop = st.sidebar.selectbox("Select Cooperative", cooperatives)
    else:
        selected_coop = current_coop  # coop users are locked to their own coop

    exporters = ['All'] + sorted(merged_df['exporter'].dropna().unique().tolist())
    selected_exporter = st.sidebar.selectbox("Select Exporter", exporters)

    # Apply filters
    filtered_df = merged_df.copy()
    if current_role == 'admin' and selected_coop != 'All':
        filtered_df = filtered_df[filtered_df['cooperative'] == selected_coop]
    if selected_exporter != 'All':
        filtered_df = filtered_df[filtered_df['exporter'] == selected_exporter]

    # ── Dashboard ─────────────────────────────────────────────────────────────
    st.title("🌾 Farmers Delivery Analytics Dashboard")

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Total Farmers", filtered_df['farmer_id'].nunique())
    with col2:
        st.metric("Total Max Quota (kg)", f"{filtered_df['max_quota_kg'].sum():,.0f}")
    with col3:
        st.metric("Total Delivered (kg)", f"{filtered_df['net_weight_kg'].sum():,.0f}")
    with col4:
        avg_delivery = (
            filtered_df['net_weight_kg'].sum() / filtered_df['max_quota_kg'].sum() * 100
        ) if filtered_df['max_quota_kg'].sum() > 0 else 0
        st.metric("Overall Delivery %", f"{avg_delivery:.1f}%")

    st.divider()
    st.header("📊 Visualizations")

    chart_col1, chart_col2 = st.columns(2)
    with chart_col1:
        st.subheader("Delivery Percentage Distribution")
        fig1 = px.histogram(filtered_df, x='delivery_percentage',
                            title='Distribution of Delivery Percentages',
                            labels={'delivery_percentage': 'Delivery Percentage (%)', 'count': 'Number of Farmers'},
                            nbins=20)
        st.plotly_chart(fig1, use_container_width=True)

    with chart_col2:
        st.subheader("Delivery Status")
        non_delivered = len(filtered_df[filtered_df['net_weight_kg'] == 0])
        delivered = len(filtered_df[filtered_df['net_weight_kg'] > 0])
        fig2 = go.Figure(data=[go.Pie(
            labels=['Delivered', 'Not Delivered'],
            values=[delivered, non_delivered],
            hole=0.3
        )])
        fig2.update_layout(title='Farmers by Delivery Status')
        st.plotly_chart(fig2, use_container_width=True)

    chart_col3, chart_col4 = st.columns(2)
    with chart_col3:
        st.subheader("Total Delivery by Exporter")
        exporter_summary = filtered_df.groupby('exporter').agg(
            net_weight_kg=('net_weight_kg', 'sum'),
            max_quota_kg=('max_quota_kg', 'sum')
        ).reset_index()
        fig3 = px.bar(exporter_summary, x='exporter', y='net_weight_kg',
                      title='Total Net Weight by Exporter',
                      labels={'net_weight_kg': 'Total Net Weight (kg)', 'exporter': 'Exporter'})
        st.plotly_chart(fig3, use_container_width=True)

    with chart_col4:
        st.subheader("Total Delivery by Cooperative")
        coop_summary = filtered_df.groupby('cooperative').agg(
            net_weight_kg=('net_weight_kg', 'sum'),
            max_quota_kg=('max_quota_kg', 'sum')
        ).reset_index()
        fig4 = px.bar(coop_summary, x='cooperative', y='net_weight_kg',
                      title='Total Net Weight by Cooperative',
                      labels={'net_weight_kg': 'Total Net Weight (kg)', 'cooperative': 'Cooperative'})
        st.plotly_chart(fig4, use_container_width=True)

    st.subheader("Delivery by Certification and Group")
    cert_col1, cert_col2 = st.columns(2)
    filtered_farmer_ids = filtered_df['farmer_id'].unique()

    with cert_col1:
        cert_coop = trace_df[trace_df['farmer_id'].isin(filtered_farmer_ids)].merge(
            filtered_df[['farmer_id', 'cooperative']], on='farmer_id', how='left'
        )
        cert_coop_summary = cert_coop.groupby(['cooperative', 'certification'])['net_weight_kg'].sum().reset_index()
        fig5 = px.bar(cert_coop_summary, x='cooperative', y='net_weight_kg', color='certification',
                      title='Delivery by Cooperative and Certification',
                      labels={'net_weight_kg': 'Total Net Weight (kg)'})
        st.plotly_chart(fig5, use_container_width=True)

    with cert_col2:
        cert_exp = trace_df[trace_df['farmer_id'].isin(filtered_farmer_ids)].copy()
        cert_exp_summary = cert_exp.groupby(['exporter', 'certification'])['net_weight_kg'].sum().reset_index()
        fig6 = px.bar(cert_exp_summary, x='exporter', y='net_weight_kg', color='certification',
                      title='Delivery by Exporter and Certification',
                      labels={'net_weight_kg': 'Total Net Weight (kg)'})
        st.plotly_chart(fig6, use_container_width=True)

    st.divider()
    st.header("📋 Detailed Tables")

    st.subheader("1. Farmers Delivery Performance")
    table1_df = filtered_df[['cooperative', 'farmer_id', 'max_quota_kg', 'net_weight_kg', 'delivery_percentage']].copy()
    table1_df = table1_df.sort_values('delivery_percentage', ascending=False)
    st.dataframe(table1_df, use_container_width=True, height=400)
    st.write(f"**Total Max Quota:** {table1_df['max_quota_kg'].sum():,.2f} kg")
    st.write(f"**Total Delivered:** {table1_df['net_weight_kg'].sum():,.2f} kg")

    st.divider()

    st.subheader("2. Farmers Who Did Not Deliver")
    non_delivery_df = filtered_df[filtered_df['net_weight_kg'] == 0][
        ['cooperative', 'farmer_id', 'max_quota_kg', 'net_weight_kg']
    ].copy()
    st.dataframe(non_delivery_df, use_container_width=True, height=400)
    st.write(f"**Number of Non-Delivering Farmers:** {len(non_delivery_df)}")
    st.write(f"**Total Undelivered Quota:** {non_delivery_df['max_quota_kg'].sum():,.2f} kg")

    st.divider()
    st.header("📥 Download Data")
    dl1, dl2 = st.columns(2)
    with dl1:
        st.download_button("Download All Farmers Data",
                           data=table1_df.to_csv(index=False).encode('utf-8'),
                           file_name='farmers_delivery_performance.csv',
                           mime='text/csv')
    with dl2:
        st.download_button("Download Non-Delivery Data",
                           data=non_delivery_df.to_csv(index=False).encode('utf-8'),
                           file_name='farmers_non_delivery.csv',
                           mime='text/csv')

except Exception as e:
    st.error(f"Error loading data: {str(e)}")
    st.write("Please check your Supabase connection and table names.")
