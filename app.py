import streamlit as st
import pandas as pd
import numpy as np
import re
import plotly.express as px
import os
from engine import run_global_reconciliation
from database import init_db, DB_FILE
from logic import (
    clean_bank_narration,
    load_category_rules,
    suggest_category,
    extract_rule_keyword,
    apply_category_rules,
    map_splitwise_category,
    SPLITWISE_CATEGORY_MAP,
    standardize_sbi_excel,
    standardize_hdfc_excel,
    standardize_splitwise_data,
    finalize_splitwise_category,
    merge_and_dedup,
)

# Ensure the Excel DB exists before the app runs
init_db()

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
        st.subheader("Fallback Category for Unmapped Splitwise Categories")
        st.caption("Each expense's own Splitwise category (Groceries, Car, Rent, etc.) is mapped automatically. This is only used when a Splitwise category has no mapping yet.")
        
        for sw_file in sw_files:
            sw_categories[sw_file.name] = st.selectbox(
                f"Fallback for '{sw_file.name}'", 
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
                skipped_files = []

                # 1. Standardize Bank Files
                if bank_files:
                    for bank_file in bank_files:
                        fname_lower = bank_file.name.lower()
                        if fname_lower.endswith(('.xlsx', '.xls')):
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
                            else:
                                # bug #4 fix: used to be dropped with zero feedback.
                                skipped_files.append(
                                    f"**{bank_file.name}** — unrecognized statement layout "
                                    f"(expected HDFC or SBI column headers in the first 30 rows)"
                                )
                        elif fname_lower.endswith('.csv'):
                            skipped_files.append(
                                f"**{bank_file.name}** — CSV bank statements aren't supported yet. "
                                f"Please export/download this statement as Excel (.xlsx/.xls) instead."
                            )
                        elif fname_lower.endswith('.pdf'):
                            skipped_files.append(
                                f"**{bank_file.name}** — PDF bank statements aren't supported yet "
                                f"(planned for a future update). Please use the Excel (.xlsx/.xls) export instead."
                            )
                        else:
                            skipped_files.append(f"**{bank_file.name}** — unsupported file type.")
                
                # 2. Standardize Splitwise Files
                if sw_files:
                    for sw_file in sw_files:
                        raw_sw_df = pd.read_csv(sw_file)
                        clean_sw = standardize_splitwise_data(raw_sw_df)
                        clean_sw = clean_sw.rename(columns={"Cost": "Total Cost", "Date": "Date"})
                        clean_sw = finalize_splitwise_category(clean_sw, sw_categories[sw_file.name])
                        new_dfs.append(clean_sw)
                
                if not new_dfs:
                    if skipped_files:
                        st.error("❌ Nothing could be processed. All uploaded files were skipped:\n\n" +
                                 "\n".join(f"- {m}" for m in skipped_files))
                    else:
                        st.error("❌ No transactions could be processed from the uploaded files.")
                    st.stop()

                if skipped_files:
                    st.warning("⚠️ Some files were skipped:\n\n" + "\n".join(f"- {m}" for m in skipped_files))
                    
                # 3. Combine newly uploaded data
                new_data = pd.concat(new_dfs, ignore_index=True)
                new_data['is_reviewed'] = False
                new_data.columns = [c.lower() for c in new_data.columns] # lowercase all columns
                
                # Ensure safety columns exist
                if 'category' not in new_data.columns: new_data['category'] = 'Uncategorized'
                if 'you_paid' not in new_data.columns: new_data['you_paid'] = 0.0
                if 'you_received' not in new_data.columns: new_data['you_received'] = 0.0
                if 'match_notes' not in new_data.columns: new_data['match_notes'] = ""
                new_data['category'] = new_data['category'].fillna('Uncategorized')

                # item #1: apply learned category_rules to whatever is still
                # Uncategorized (mainly bank Debit/Credit rows -- Splitwise rows
                # already got a category from their own Splitwise category, item #4).
                new_data = apply_category_rules(new_data)

                # bug #8 fix: see logic.merge_and_dedup for why a plain
                # (date, description, amount, account) key isn't safe on its own.
                # 4. Merge with Existing Database
                existing_df = pd.read_excel(DB_FILE, sheet_name='transactions')
                combined_df = merge_and_dedup(existing_df, new_data)
                    
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
            # item #1: learn from any category a human just set/corrected, so
            # the same merchant is auto-categorized next time instead of
            # landing back in this queue.
            rules = load_category_rules()
            learned_count = 0
            for idx, edited_row in edited_df.iterrows():
                new_cat = str(edited_row['category']).strip()
                old_cat = str(df.at[idx, 'category']).strip()
                if new_cat and new_cat not in ('Uncategorized', 'Settlement', 'Excluded', 'nan') and new_cat != old_cat:
                    desc_clean = clean_bank_narration(edited_row['description'])
                    keyword = extract_rule_keyword(desc_clean)
                    if keyword:
                        rules[keyword] = new_cat
                        learned_count += 1

            df.update(edited_df)

            rules_df = pd.DataFrame(
                sorted(rules.items()), columns=['keyword', 'category']
            ) if rules else pd.read_excel(DB_FILE, sheet_name='category_rules')
            with pd.ExcelWriter(DB_FILE, engine='openpyxl') as writer:
                df.to_excel(writer, sheet_name='transactions', index=False)
                rules_df.to_excel(writer, sheet_name='category_rules', index=False)

            if learned_count:
                st.success(f"Database updated! Learned {learned_count} new categorization rule(s) for next time.")
            else:
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