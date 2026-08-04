import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import os
from engine import run_global_reconciliation
from database import init_db, DB_FILE

# Ensure the Excel DB exists before the app runs
init_db()

def standardize_sbi_excel(df):
    """
    Cleans and standardizes SBI Excel bank statements.
    """
    # 1. Find the actual header row (look for "Details" or "Debit" in any column)
    header_idx = None
    for idx, row in df.iterrows():
        row_str = row.astype(str).str.lower()
        if 'details' in row_str.values and 'debit' in row_str.values:
            header_idx = idx
            break
            
    if header_idx is None:
        raise ValueError("Could not find the header row containing 'Details' and 'Debit'. Ensure this is a valid SBI statement.")

    # 2. Rebuild the dataframe starting from the actual header
    new_cols = df.iloc[header_idx].astype(str).str.strip().tolist()
    clean_df = df.iloc[header_idx + 1:].copy() 
    clean_df.columns = new_cols

    # 3. Drop rows that are entirely NaN or footer metadata
    clean_df = clean_df.dropna(how='all')
    # Filter out empty dates or summary lines at the bottom
    clean_df = clean_df.dropna(subset=['Date']) 
    clean_df = clean_df[clean_df['Date'].astype(str).str.strip() != '']
    
    # 4. Standardize column names
    column_mapping = {
        "Date": "Date",
        "Details": "Description",
        "Ref No/Cheque No": "Reference",
        "Debit": "Debit_Amount",
        "Credit": "Credit_Amount",
        "Balance": "Balance"
    }
    clean_df = clean_df.rename(columns=column_mapping)
    
    # 5. Clean up string data (remove \n from descriptions)
    clean_df['Description'] = clean_df['Description'].astype(str).str.replace('\n', '', regex=False)
    
    # 6. Clean and Merge Amounts
    if 'Debit_Amount' in clean_df.columns and 'Credit_Amount' in clean_df.columns:
        # Convert amounts to numeric, handle missing values
        clean_df['Debit_Amount'] = pd.to_numeric(clean_df['Debit_Amount'].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
        clean_df['Credit_Amount'] = pd.to_numeric(clean_df['Credit_Amount'].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
        
        # Unified Amount and Type columns
        clean_df['Amount'] = np.where(clean_df['Debit_Amount'] > 0, clean_df['Debit_Amount'], clean_df['Credit_Amount'])
        clean_df['Type'] = np.where(clean_df['Debit_Amount'] > 0, 'Debit', 'Credit')
        
    # 7. Final cleanup
    # SBI dates are usually DD/MM/YYYY or DD-MM-YYYY
    clean_df['Date'] = pd.to_datetime(clean_df['Date'], dayfirst=True, errors='coerce')
    clean_df = clean_df.dropna(subset=['Date', 'Amount']) 
    clean_df['Account_Source'] = 'SBI Bank'
    
    return clean_df

def standardize_hdfc_excel(df):
    """
    Cleans and standardizes HDFC Excel bank statements.
    """
    # 1. Find the actual header row (look for "Narration" or "Date" in any column)
    header_idx = None
    for idx, row in df.iterrows():
        row_str = row.astype(str).str.lower()
        if 'narration' in row_str.values and 'date' in row_str.values:
            header_idx = idx
            break
            
    if header_idx is None:
        raise ValueError("Could not find the header row containing 'Date' and 'Narration'. Ensure this is a valid HDFC statement.")

    # 2. Rebuild the dataframe starting from the actual header
    new_cols = df.iloc[header_idx].astype(str).str.strip().tolist()
    clean_df = df.iloc[header_idx + 2:].copy() 
    clean_df.columns = new_cols

    # 3. Drop rows that are entirely NaN or just trailing metadata
    clean_df = clean_df.dropna(how='all')
    clean_df = clean_df[~clean_df['Date'].astype(str).str.contains('Statement', case=False, na=False)]
    
    # 4. Standardize the column names
    column_mapping = {
        "Date": "Date",
        "Narration": "Description",
        "Chq./Ref.No.": "Reference",
        "Value Dt": "Value_Date",
        "Withdrawal Amt.": "Debit_Amount",
        "Deposit Amt.": "Credit_Amount",
        "Closing Balance": "Balance"
    }
    
    clean_df = clean_df.rename(columns=column_mapping)
    
    # 5. Clean and Merge Amounts
    if 'Debit_Amount' in clean_df.columns and 'Credit_Amount' in clean_df.columns:
        clean_df['Debit_Amount'] = pd.to_numeric(clean_df['Debit_Amount'].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
        clean_df['Credit_Amount'] = pd.to_numeric(clean_df['Credit_Amount'].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
        
        clean_df['Amount'] = np.where(clean_df['Debit_Amount'] > 0, clean_df['Debit_Amount'], clean_df['Credit_Amount'])
        clean_df['Type'] = np.where(clean_df['Debit_Amount'] > 0, 'Debit', 'Credit')

    # 6. Final cleanup of essential columns
    clean_df['Date'] = pd.to_datetime(clean_df['Date'], format='%d/%m/%y', errors='coerce')
    clean_df = clean_df.dropna(subset=['Date', 'Amount']) 
    
    # ADD THIS LINE: Explicitly tag this data as coming from the Bank
    clean_df['Account_Source'] = 'HDFC Bank'
    
    return clean_df

def standardize_splitwise_data(df, user_name="Rishabh Agrawal"):
    """
    Converts the Splitwise net balance matrix into standardized transaction rows.

    Three kinds of money movement come out of a Splitwise export, and they
    need to be treated differently:
      - A group bill you fronted -> 'you_paid' (matched to a bank Debit)
      - A settlement paid back to you -> 'you_received' (matched to a bank Credit)
      - Your real personal share of an expense -> 'your_share' / 'Amount'
    Settlements ("Payment" rows) are never a real expense (bug #2 fix) -- they're
    just cash moving between people and must not inflate category spend.
    """
    if user_name not in df.columns:
        raise ValueError(f"User '{user_name}' not found in Splitwise columns. Please check your exact Splitwise display name.")

    df = df.copy()

    # --- bug #1 fix: drop Splitwise's own running-balance summary row and any
    # blank/footer rows before doing any arithmetic on them. ---
    df['Description'] = df['Description'].astype(str).str.strip()
    df = df[df['Description'].str.lower() != 'total balance']
    df = df[df['Category'].notna() & (df['Category'].astype(str).str.strip() != '')]
    df = df[df['Date'].notna()]

    df['Cost'] = pd.to_numeric(df['Cost'].astype(str).str.replace(',', ''), errors='coerce').fillna(0)

    you_paid, your_share, you_received, txn_type, settlement_category = [], [], [], [], []

    for _, row in df.iterrows():
        cost = row['Cost']
        net = row[user_name]
        if pd.isna(net):
            net = 0
        is_settlement = str(row['Category']).strip().lower() == 'payment'

        if is_settlement:
            # bug #2 fix: settlements move cash, they are not an expense.
            paid = cost if net > 0 else 0.0       # you paid a roommate back
            received = cost if net < 0 else 0.0   # a roommate paid you back
            share = 0.0
            ttype = 'Settlement'
            cat = 'Settlement'
        else:
            received = 0.0
            if net == 0:
                paid, share = 0.0, 0.0
            elif net > 0:
                # You paid the bill, you are owed the difference
                paid, share = cost, cost - net
            else:
                # Someone else paid, you owe them this amount
                paid, share = 0.0, abs(net)
            ttype = 'Debit'
            cat = None  # keep the group-level default category chosen at upload

        you_paid.append(paid)
        your_share.append(share)
        you_received.append(received)
        txn_type.append(ttype)
        settlement_category.append(cat)

    df['you_paid'] = you_paid
    df['your_share'] = your_share
    df['you_received'] = you_received
    df['Type'] = txn_type
    df['_settlement_category'] = settlement_category  # None unless it's a settlement row

    # 'Amount' is always your true personal expense share -- 0 for settlements.
    df['Amount'] = your_share
    df['Account_Source'] = 'Splitwise'
    df['Match_Notes'] = ""

    # Keep: real personal expenses, bills you fronted, or settlements -- all three
    # need to either show up in Analysis or be matched against the bank leg.
    df = df[(df['Amount'] > 0) | (df['you_paid'] > 0) | (df['you_received'] > 0)].copy()

    return df
# --- STREAMLIT UI ---

st.set_page_config(page_title="Finance Dashboard", layout="wide")
st.title("Personal Finance Tracker")

tab_upload, tab_triage, tab_analysis = st.tabs(["Upload & Sync", "Triage Queue", "Expense Analysis"])

with tab_upload:
    st.header("Upload Statements")
    col1, col2 = st.columns(2)
    
    with col1:
        # Accept multiple bank files (Optional)
        bank_files = st.file_uploader("Upload Bank File(s) (Optional)", type=['csv', 'pdf', 'xlsx', 'xls'], accept_multiple_files=True)
    with col2:
        # Accept multiple Splitwise files (Optional)
        sw_files = st.file_uploader("Upload Splitwise CSV(s) (Optional)", type=['csv'], accept_multiple_files=True)
        
    # Dynamically create category dropdowns for Splitwise files BEFORE processing
    sw_categories = {}
    if sw_files:
        st.write("---")
        st.subheader("Assign Default Categories for Splitwise Groups")
        
        for sw_file in sw_files:
            sw_categories[sw_file.name] = st.selectbox(
                f"Category for '{sw_file.name}'", 
                # Updated options list:
                options=["Uncategorized", "Groceries", "Eating Out", "Party", "Transport", "Utilities", "Shopping", "Home Setup", "Travel - Weekend", "Travel - Major", "Rent", "General"],
                key=f"cat_{sw_file.name}"
            )
        st.write("---")
        
    if st.button("Process Files"):
        if not bank_files and not sw_files:
            st.warning("⚠️ Please upload at least one Bank statement or Splitwise CSV to process.")
        else:
            try:
                new_dfs = []
                
                # 1. Standardize Bank Files
                if bank_files:
                    for bank_file in bank_files:
                        if bank_file.name.endswith(('.xlsx', '.xls')):
                            raw_bank_df = pd.read_excel(bank_file, header=None)
                            
                            # Auto-Detect
                            bank_type = "Unknown"
                            for _, row in raw_bank_df.head(30).iterrows():
                                row_str = row.astype(str).str.lower()
                                if 'narration' in row_str.values and 'withdrawal amt.' in row_str.values:
                                    bank_type = "HDFC"
                                    break
                                elif 'details' in row_str.values and 'debit' in row_str.values:
                                    bank_type = "SBI"
                                    break
                                    
                            if bank_type == "HDFC":
                                new_dfs.append(standardize_hdfc_excel(raw_bank_df))
                            elif bank_type == "SBI":
                                new_dfs.append(standardize_sbi_excel(raw_bank_df))
                
                # 2. Standardize Splitwise Files
                if sw_files:
                    for sw_file in sw_files:
                        raw_sw_df = pd.read_csv(sw_file)
                        clean_sw = standardize_splitwise_data(raw_sw_df)
                        clean_sw = clean_sw.rename(columns={"Cost": "Total Cost", "Date": "Date"})
                        # Settlement rows keep 'Settlement' as their category (bug #2);
                        # every other row gets the group's chosen default category.
                        clean_sw['Category'] = clean_sw['_settlement_category'].fillna(sw_categories[sw_file.name])
                        clean_sw = clean_sw.drop(columns=['_settlement_category'])
                        new_dfs.append(clean_sw)
                
                if not new_dfs:
                    st.stop()
                    
                # 3. Combine newly uploaded data
                new_data = pd.concat(new_dfs, ignore_index=True)
                new_data['is_reviewed'] = False
                new_data.columns = [c.lower() for c in new_data.columns] # lowercase all columns
                
                # Ensure safety columns exist
                if 'category' not in new_data.columns: new_data['category'] = 'Uncategorized'
                if 'you_paid' not in new_data.columns: new_data['you_paid'] = 0.0
                if 'you_received' not in new_data.columns: new_data['you_received'] = 0.0
                if 'match_notes' not in new_data.columns: new_data['match_notes'] = ""

                # bug #8 fix: a plain (date, description, amount, account) key collapses
                # two *genuinely different* same-day transactions with identical text/amount
                # (e.g. two ₹121 "Auto" rides) into one. Tag each row with its occurrence
                # index within its own upload batch — computed BEFORE merging/sorting, so a
                # re-upload of the same file reproduces the same indices and still dedupes
                # correctly, while distinct look-alike rows keep their own identity.
                DEDUP_COLUMNS = ['date', 'description', 'amount', 'account_source']

                def _ensure_dup_seq(frame):
                    frame = frame.copy()
                    if '_dup_seq' not in frame.columns or frame['_dup_seq'].isna().any():
                        frame['_dup_seq'] = frame.groupby(DEDUP_COLUMNS).cumcount()
                    else:
                        frame['_dup_seq'] = frame['_dup_seq'].astype(int)
                    return frame

                new_data = _ensure_dup_seq(new_data)

                # 4. Merge with Existing Database
                existing_df = pd.read_excel(DB_FILE, sheet_name='transactions')
                if not existing_df.empty:
                    existing_df = _ensure_dup_seq(existing_df)
                    combined_df = pd.concat([existing_df, new_data], ignore_index=True)
                    combined_df = combined_df.sort_values(by='is_reviewed', ascending=False)

                    # Deduplicate BEFORE matching (ignoring 'type' so we don't duplicate items that changed to Transfer)
                    combined_df = combined_df.drop_duplicates(subset=DEDUP_COLUMNS + ['_dup_seq'], keep='first')
                else:
                    combined_df = new_data
                    
                # 5. RUN GLOBAL RECONCILIATION
                from engine import run_global_reconciliation
                final_df = run_global_reconciliation(combined_df)
                
                # 6. Save back to Excel
                new_rows = len(final_df) - len(existing_df) if not existing_df.empty else len(final_df)
                
                rules_df = pd.read_excel(DB_FILE, sheet_name='category_rules')
                with pd.ExcelWriter(DB_FILE, engine='openpyxl') as writer:
                    final_df.to_excel(writer, sheet_name='transactions', index=False)
                    rules_df.to_excel(writer, sheet_name='category_rules', index=False)
                    
                st.success(f"✅ Processed successfully! {new_rows} new transactions added. Global matching applied.")
                
            except Exception as e:
                st.error(f"❌ An unexpected error occurred: {e}")
with tab_triage:
    st.header("Triage Queue (Review & Categorize)")
    
    df = pd.read_excel(DB_FILE, sheet_name='transactions')
    
    # 1. Ensure columns exist
    if 'match_notes' not in df.columns: df['match_notes'] = ""
    if 'you_paid' not in df.columns: df['you_paid'] = 0.0
    if 'you_received' not in df.columns: df['you_received'] = 0.0
    if 'your_share' not in df.columns: df['your_share'] = df['amount']
        
    # 2. FIX: Convert Excel 'NaN' blanks back into standard empty strings and zeros
    df['match_notes'] = df['match_notes'].fillna("")
    df['you_paid'] = df['you_paid'].fillna(0.0)
    df['you_received'] = df['you_received'].fillna(0.0)
    df['your_share'] = df['your_share'].fillna(df['amount'])
        
    unreviewed_mask = df['is_reviewed'] == False
    unreviewed_df = df[unreviewed_mask]
    
    if not unreviewed_df.empty:
        st.write("Confirm Categories and Auto-detected Transfers:")
        
        display_cols = ['date', 'description', 'amount', 'you_paid', 'you_received', 'your_share', 'account_source', 'is_reviewed', 'category', 'type', 'match_notes']
        ui_df = unreviewed_df[display_cols]
        
      
        edited_df = st.data_editor(
            ui_df,
            column_config={
                "date": st.column_config.Column("Date", disabled=True),
                "description": st.column_config.TextColumn("Description", disabled=True),
                "amount": st.column_config.NumberColumn("Amount", format="₹%.2f", disabled=True),
                "you_paid": st.column_config.NumberColumn("You Paid (Bank Match)", format="₹%.2f", disabled=True),
                "you_received": st.column_config.NumberColumn("You Received (Bank Match)", format="₹%.2f", disabled=True),
                "your_share": st.column_config.NumberColumn("Your True Expense", format="₹%.2f", disabled=True),
                "account_source": st.column_config.TextColumn("Source", disabled=True),
                "is_reviewed": st.column_config.CheckboxColumn("Reviewed ✅", default=False),
                "category": st.column_config.SelectboxColumn(
                    "Category", 
                    # Updated options list:
                    options=["Groceries", "Eating Out", "Party", "Transport", "Utilities", "Shopping", "Home Setup", "Travel - Weekend", "Travel - Major", "Rent", "General", "Demat Transfer", "Settlement", "Excluded", "Uncategorized"]
                ),
                "type": st.column_config.SelectboxColumn(
                    "Type", 
                    options=["Debit", "Credit", "Transfer", "Transfer_Splitwise_Base", "Transfer_Splitwise_Settlement", "Settlement"]
                ),
                "match_notes": st.column_config.TextColumn("Match Info (Audit)", disabled=True)
            },
            hide_index=False 
        )
        
        if st.button("Save Changes to Database"):
            df.update(edited_df)
            
            rules_df = pd.read_excel(DB_FILE, sheet_name='category_rules')
            with pd.ExcelWriter(DB_FILE, engine='openpyxl') as writer:
                df.to_excel(writer, sheet_name='transactions', index=False)
                rules_df.to_excel(writer, sheet_name='category_rules', index=False)
                
            st.success("Database updated!")
            st.rerun() 
    else:
        st.info("No new transactions to review.")

    # ==========================================
    # --- MANUAL MATCHER TOOL ---
    # ==========================================
    st.divider()
    st.subheader("🔗 Manual Matcher")
    st.write("Link Splitwise payments with Bank debits that the auto-engine missed (e.g., differing amounts or dates).")
    
    # Find candidates that have NO match notes
    unmatched_bank = df[(df['account_source'].isin(['HDFC Bank', 'SBI Bank', 'Bank'])) & (df['type'] == 'Debit') & (df['match_notes'] == '')]
    unmatched_sw = df[(df['account_source'] == 'Splitwise') & (df['you_paid'] > 0) & (df['match_notes'] == '')]
    
    if not unmatched_bank.empty and not unmatched_sw.empty:
        col_m1, col_m2 = st.columns(2)
        
        with col_m1:
            # Create a readable dropdown format: [Row ID] Date | Description | Amount
            bank_opts = unmatched_bank.apply(
                lambda r: f"[{r.name}] {r['date'].strftime('%d %b %Y')} | {r['description']} | ₹{r['amount']}", axis=1
            ).tolist()
            selected_bank_str = st.selectbox("Select Unmatched Bank Debit", ["-- Select --"] + bank_opts)
            
        with col_m2:
            sw_opts = unmatched_sw.apply(
                lambda r: f"[{r.name}] {r['date'].strftime('%d %b %Y')} | {r['description']} | Paid: ₹{r['you_paid']}", axis=1
            ).tolist()
            selected_sw_str = st.selectbox("Select Unmatched Splitwise Payment", ["-- Select --"] + sw_opts)
            
        if st.button("Manually Link Transactions"):
            if selected_bank_str != "-- Select --" and selected_sw_str != "-- Select --":
                import re
                
                # Extract the hidden row ID from the brackets e.g., "[42]" -> 42
                b_idx = int(re.search(r'\[(\d+)\]', selected_bank_str).group(1))
                s_idx = int(re.search(r'\[(\d+)\]', selected_sw_str).group(1))
                
                # Apply the manual link
                df.at[b_idx, 'type'] = 'Transfer_Splitwise_Base'
                df.at[b_idx, 'category'] = 'Excluded'
                df.at[b_idx, 'match_notes'] = f"🔗 Matched SW: {df.at[s_idx, 'description']}"
                
                df.at[s_idx, 'match_notes'] = f"🔗 Matched Bank: {df.at[b_idx, 'description']}"
                
                # Save to Database
                rules_df = pd.read_excel(DB_FILE, sheet_name='category_rules')
                with pd.ExcelWriter(DB_FILE, engine='openpyxl') as writer:
                    df.to_excel(writer, sheet_name='transactions', index=False)
                    rules_df.to_excel(writer, sheet_name='category_rules', index=False)
                    
                st.success("✅ Successfully linked transactions!")
                st.rerun()
            else:
                st.warning("Please select both a Bank transaction and a Splitwise transaction to link them.")
    else:
        st.info("No unmatched transactions available for manual linking.")

    # ==========================================
    # --- MANUAL MATCHER TOOL: SETTLEMENTS RECEIVED (bug #6 counterpart) ---
    # ==========================================
    st.divider()
    st.subheader("🔗 Manual Matcher — Settlements received")
    st.write("Link a roommate's Splitwise repayment with the incoming Bank credit that the auto-engine missed.")

    unmatched_bank_credit = df[(df['account_source'].isin(['HDFC Bank', 'SBI Bank', 'Bank'])) & (df['type'] == 'Credit') & (df['match_notes'] == '')]
    unmatched_sw_received = df[(df['account_source'] == 'Splitwise') & (df['you_received'] > 0) & (df['match_notes'] == '')]

    if not unmatched_bank_credit.empty and not unmatched_sw_received.empty:
        col_r1, col_r2 = st.columns(2)

        with col_r1:
            credit_opts = unmatched_bank_credit.apply(
                lambda r: f"[{r.name}] {r['date'].strftime('%d %b %Y')} | {r['description']} | ₹{r['amount']}", axis=1
            ).tolist()
            selected_credit_str = st.selectbox("Select Unmatched Bank Credit", ["-- Select --"] + credit_opts, key="credit_select")

        with col_r2:
            sw_recv_opts = unmatched_sw_received.apply(
                lambda r: f"[{r.name}] {r['date'].strftime('%d %b %Y')} | {r['description']} | Received: ₹{r['you_received']}", axis=1
            ).tolist()
            selected_sw_recv_str = st.selectbox("Select Unmatched Splitwise Settlement", ["-- Select --"] + sw_recv_opts, key="sw_recv_select")

        if st.button("Manually Link Settlement"):
            if selected_credit_str != "-- Select --" and selected_sw_recv_str != "-- Select --":
                import re

                c_idx = int(re.search(r'\[(\d+)\]', selected_credit_str).group(1))
                s_idx = int(re.search(r'\[(\d+)\]', selected_sw_recv_str).group(1))

                df.at[c_idx, 'type'] = 'Transfer_Splitwise_Settlement'
                df.at[c_idx, 'category'] = 'Excluded'
                df.at[c_idx, 'match_notes'] = f"🔗 Matched SW: {df.at[s_idx, 'description']}"

                df.at[s_idx, 'match_notes'] = f"🔗 Matched Bank: {df.at[c_idx, 'description']}"

                rules_df = pd.read_excel(DB_FILE, sheet_name='category_rules')
                with pd.ExcelWriter(DB_FILE, engine='openpyxl') as writer:
                    df.to_excel(writer, sheet_name='transactions', index=False)
                    rules_df.to_excel(writer, sheet_name='category_rules', index=False)

                st.success("✅ Successfully linked settlement!")
                st.rerun()
            else:
                st.warning("Please select both a Bank credit and a Splitwise settlement to link them.")
    else:
        st.info("No unmatched settlements available for manual linking.")

with tab_analysis:
    st.header("Expense Analysis")
    
    df = pd.read_excel(DB_FILE, sheet_name='transactions')
    
    clean_df = df[
        (df['is_reviewed'] == True) & 
        (df['type'] == 'Debit') & 
        (df['category'] != 'Excluded') & 
        (df['category'] != 'Settlement') &
        (~df['category'].astype(str).str.contains('Transfer', case=False, na=False))
    ].copy()
    
    if not clean_df.empty:
        clean_df['date'] = pd.to_datetime(clean_df['date'])
        
        col_chart1, col_chart2 = st.columns(2)
        
        with col_chart1:
            st.subheader("Monthly Expenses")
            clean_df['Month'] = clean_df['date'].dt.to_period('M').astype(str)
            monthly_trend = clean_df.groupby('Month')['amount'].sum().reset_index()
            
            fig_bar = px.bar(
                monthly_trend, x='Month', y='amount', text='amount', 
                title="Total Spend per Month", color_discrete_sequence=['#4C72B0']
            )
            fig_bar.update_traces(texttemplate='₹%{text:.2s}', textposition='outside')
            st.plotly_chart(fig_bar, use_container_width=True)
            
        with col_chart2:
            st.subheader("Category Breakdown")
            cat_breakdown = clean_df.groupby('category')['amount'].sum().reset_index()
            
            fig_pie = px.pie(
                cat_breakdown, values='amount', names='category', 
                title="Where is your money going?", hole=0.4
            )
            st.plotly_chart(fig_pie, use_container_width=True)
            
        st.subheader("Raw Data Summary")
        st.dataframe(clean_df.sort_values(by='date', ascending=False), use_container_width=True)
    else:
        st.warning("No reviewed expense data available to analyze yet. Please triage your uploads.")