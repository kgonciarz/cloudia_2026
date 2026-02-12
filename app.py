import streamlit as st
import pandas as pd
from supabase import create_client, Client
import plotly.express as px
import plotly.graph_objects as go
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Supabase connection
st.set_page_config(page_title="Farmers Analytics", layout="wide")

# Initialize Supabase client
def init_supabase():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        st.error("Please set SUPABASE_URL and SUPABASE_KEY in .env file")
        st.stop()
    return create_client(url, key)

supabase = init_supabase()

# Load data
@st.cache_data(ttl=300)
def load_data():
    # Function to fetch all rows with pagination (based on working example)
    def load_data_batched(table_name, page_size=1000):
        """Fetches data from a Supabase table in batches."""
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
            st.error(f"An error occurred during batched data loading from '{table_name}': {e}")
            return pd.DataFrame()
    
    # Get farmers data
    st.info("Loading farmers data...")
    farmers_df = load_data_batched('farmers')
    
    # Get traceability data  
    st.info("Loading traceability data...")
    trace_df = load_data_batched('traceability')
    
    st.success(f"✓ Loaded {len(farmers_df)} farmers and {len(trace_df)} traceability records")
    
    return farmers_df, trace_df

try:
    farmers_df, trace_df = load_data()
    
    # Standardize farmer_id in both dataframes (lowercase and trim)
    farmers_df['farmer_id'] = farmers_df['farmer_id'].astype(str).str.strip().str.lower()
    trace_df['farmer_id'] = trace_df['farmer_id'].astype(str).str.strip().str.lower()
    
    # Aggregate net_weight_kg per farmer - handle None values
    trace_agg = trace_df.groupby('farmer_id').agg({
        'net_weight_kg': 'sum',
        'certification': lambda x: ', '.join([str(val) for val in x.dropna().unique() if str(val).strip() != '']),
        'exporter': 'first'
    }).reset_index()
    
    # Replace empty strings with 'Unknown'
    trace_agg['certification'] = trace_agg['certification'].replace('', 'Unknown')
    trace_agg['exporter'] = trace_agg['exporter'].fillna('Unknown')
    
    # Left join farmers with aggregated traceability
    merged_df = farmers_df.merge(trace_agg, on='farmer_id', how='left')
    merged_df['net_weight_kg'] = merged_df['net_weight_kg'].fillna(0)
    merged_df['certification'] = merged_df['certification'].fillna('Unknown')
    merged_df['exporter'] = merged_df['exporter'].fillna('Unknown')
    
    # Calculate percentage delivered
    merged_df['delivery_percentage'] = (merged_df['net_weight_kg'] / merged_df['max_quota_kg'] * 100).round(2)
    merged_df['delivery_percentage'] = merged_df['delivery_percentage'].fillna(0)
    
    # Sidebar filters
    st.sidebar.header("Filters")
    
    cooperatives = ['All'] + sorted(merged_df['cooperative'].unique().tolist())
    selected_coop = st.sidebar.selectbox("Select Cooperative", cooperatives)
    
    exporters = ['All'] + sorted(merged_df['exporter'].dropna().unique().tolist())
    selected_exporter = st.sidebar.selectbox("Select Exporter", exporters)
    
    # Apply filters
    filtered_df = merged_df.copy()
    if selected_coop != 'All':
        filtered_df = filtered_df[filtered_df['cooperative'] == selected_coop]
    if selected_exporter != 'All':
        filtered_df = filtered_df[filtered_df['exporter'] == selected_exporter]
    
    # Main title
    st.title("🌾 Farmers Delivery Analytics Dashboard")
    
    # Debug info - show raw totals before filtering
    with st.expander("📊 Debug Info - Raw Data Totals (before filters)", expanded=True):
        st.write(f"**Raw Farmers Count:** {len(farmers_df)}")
        st.write(f"**Raw Traceability Records:** {len(trace_df)}")
        st.write(f"**Raw Total Net Weight (all traceability):** {trace_df['net_weight_kg'].sum():,.2f} kg")
        
        st.write("---")
        st.write("**🔍 Duplicate Check:**")
        duplicate_farmers = farmers_df['farmer_id'].duplicated().sum()
        st.write(f"⚠️  Duplicate farmer_ids in FARMERS table: **{duplicate_farmers}**")
        
        st.write("---")
        st.write(f"**Merged Data Rows:** {len(merged_df)}")
        st.write(f"**Merged Total Net Weight:** {merged_df['net_weight_kg'].sum():,.2f} kg")
        st.write(f"**Farmers after merge:** {merged_df['farmer_id'].nunique()}")
        
        inflation = merged_df['net_weight_kg'].sum() - trace_df['net_weight_kg'].sum()
        if inflation > 0:
            st.error(f"⚠️  DATA INFLATION: {inflation:,.2f} kg ({(inflation/trace_df['net_weight_kg'].sum()*100):.2f}%) - likely due to duplicate farmer_ids in farmers table!")
        elif inflation < 0:
            st.warning(f"⚠️  DATA LOSS: {abs(inflation):,.2f} kg ({(abs(inflation)/trace_df['net_weight_kg'].sum()*100):.2f}%)")
        else:
            st.success("✓ No data inflation or loss")
    
    # Key Metrics
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Total Farmers", filtered_df['farmer_id'].str.lower().nunique())
    with col2:
        st.metric("Total Max Quota (kg)", f"{filtered_df['max_quota_kg'].sum():,.0f}")
    with col3:
        st.metric("Total Delivered (kg)", f"{filtered_df['net_weight_kg'].sum():,.0f}")
    with col4:
        avg_delivery = (filtered_df['net_weight_kg'].sum() / filtered_df['max_quota_kg'].sum() * 100) if filtered_df['max_quota_kg'].sum() > 0 else 0
        st.metric("Overall Delivery %", f"{avg_delivery:.1f}%")
    
    st.divider()
    
    # Charts Section
    st.header("📊 Visualizations")
    
    chart_col1, chart_col2 = st.columns(2)
    
    with chart_col1:
        # Delivery percentage distribution
        st.subheader("Delivery Percentage Distribution")
        fig1 = px.histogram(filtered_df, x='delivery_percentage', 
                           title='Distribution of Delivery Percentages',
                           labels={'delivery_percentage': 'Delivery Percentage (%)', 'count': 'Number of Farmers'},
                           nbins=20)
        st.plotly_chart(fig1, use_container_width=True)
        
    with chart_col2:
        # Non-delivery analysis
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
        # Total delivery by exporter
        st.subheader("Total Delivery by Exporter")
        exporter_summary = filtered_df.groupby('exporter').agg({
            'net_weight_kg': 'sum',
            'max_quota_kg': 'sum'
        }).reset_index()
        
        fig3 = px.bar(exporter_summary, x='exporter', y='net_weight_kg',
                     title='Total Net Weight by Exporter',
                     labels={'net_weight_kg': 'Total Net Weight (kg)', 'exporter': 'Exporter'})
        st.plotly_chart(fig3, use_container_width=True)
    
    with chart_col4:
        # Total by cooperative
        st.subheader("Total Delivery by Cooperative")
        coop_summary = filtered_df.groupby('cooperative').agg({
            'net_weight_kg': 'sum',
            'max_quota_kg': 'sum'
        }).reset_index()
        
        fig4 = px.bar(coop_summary, x='cooperative', y='net_weight_kg',
                     title='Total Net Weight by Cooperative',
                     labels={'net_weight_kg': 'Total Net Weight (kg)', 'cooperative': 'Cooperative'})
        st.plotly_chart(fig4, use_container_width=True)
    
    # Certification analysis
    st.subheader("Delivery by Certification and Group")
    
    cert_col1, cert_col2 = st.columns(2)
    
    with cert_col1:
        # By cooperative and certification
        cert_coop = trace_df.merge(farmers_df[['farmer_id', 'cooperative']], on='farmer_id', how='left')
        cert_coop_summary = cert_coop.groupby(['cooperative', 'certification'])['net_weight_kg'].sum().reset_index()
        
        fig5 = px.bar(cert_coop_summary, x='cooperative', y='net_weight_kg', color='certification',
                     title='Delivery by Cooperative and Certification',
                     labels={'net_weight_kg': 'Total Net Weight (kg)'})
        st.plotly_chart(fig5, use_container_width=True)
    
    with cert_col2:
        # By exporter and certification
        cert_exp_summary = trace_df.groupby(['exporter', 'certification'])['net_weight_kg'].sum().reset_index()
        
        fig6 = px.bar(cert_exp_summary, x='exporter', y='net_weight_kg', color='certification',
                     title='Delivery by Exporter and Certification',
                     labels={'net_weight_kg': 'Total Net Weight (kg)'})
        st.plotly_chart(fig6, use_container_width=True)
    
    st.divider()
    
    # Tables Section
    st.header("📋 Detailed Tables")
    
    # Table 1: All farmers with delivery data
    st.subheader("1. Farmers Delivery Performance")
    table1_df = filtered_df[['cooperative', 'farmer_id', 'max_quota_kg', 'net_weight_kg', 'delivery_percentage']].copy()
    table1_df = table1_df.sort_values('delivery_percentage', ascending=False)
    
    st.dataframe(table1_df, use_container_width=True, height=400)
    
    # Summary for table 1
    st.write(f"**Total Max Quota:** {table1_df['max_quota_kg'].sum():,.2f} kg")
    st.write(f"**Total Delivered:** {table1_df['net_weight_kg'].sum():,.2f} kg")
    
    st.divider()
    
    # Table 2: Farmers who did not deliver
    st.subheader("2. Farmers Who Did Not Deliver")
    non_delivery_df = filtered_df[filtered_df['net_weight_kg'] == 0][['cooperative', 'farmer_id', 'max_quota_kg', 'net_weight_kg']].copy()
    
    st.dataframe(non_delivery_df, use_container_width=True, height=400)
    
    # Summary for table 2
    st.write(f"**Number of Non-Delivering Farmers:** {len(non_delivery_df)}")
    st.write(f"**Total Undelivered Quota:** {non_delivery_df['max_quota_kg'].sum():,.2f} kg")
    
    # Download buttons
    st.divider()
    st.header("📥 Download Data")
    
    download_col1, download_col2 = st.columns(2)
    
    with download_col1:
        csv1 = table1_df.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="Download All Farmers Data",
            data=csv1,
            file_name='farmers_delivery_performance.csv',
            mime='text/csv'
        )
    
    with download_col2:
        csv2 = non_delivery_df.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="Download Non-Delivery Data",
            data=csv2,
            file_name='farmers_non_delivery.csv',
            mime='text/csv'
        )

except Exception as e:
    st.error(f"Error loading data: {str(e)}")
    st.write("Please check your Supabase connection and table names.")