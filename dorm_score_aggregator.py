import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
import os
from datetime import datetime

# --- Configuration ---
# !!! IMPORTANT: Replace with your actual file path and sheet names !!!
SERVICE_ACCOUNT_FILE = 'path/to/your/service_account_key.json'
# Example: 'google_service_credentials.json'
# Ensure this JSON file is in the same directory as the script or provide the full path.

GOOGLE_SHEET_NAME = 'Dorm Scores Sheet' # The name of your Google Spreadsheet
DATA_SHEET_TAB_NAME = 'Daily Dorm Scores' # The tab within the sheet that has the raw data

# Define output directory for CSV files
OUTPUT_DIR = 'aggregated_scores'

# Define score columns and their maximums (based on Step 1 clarifications)
# Format: 'Column Name': max_score
# For scores with no fixed max from user, or where max is context-dependent for % calculation,
# we might need a different approach or an additional 'Max_Score' column in the raw data.
# For now, using the fixed maximums discussed.
SCORE_COLUMNS_MAX = {
    'Stairs Score': 10,
    'Bed Made Score': 10,
    'Bedside Table Score': 10,
    'ShoesAligned Score': 10,
    'Big Shelf Score': 10,
    'Charger Cables Score': 10,
    'Nothing on Ground Score': 10,
    'Clothes in Locker Score': 10,
    'Bathroom Score': 10,
    'Turn Off Light Score': 15, # Applicable Grades 9-12
    'Common Room Score': 20,
    'Bonus Score': 10,
    # 'Daily Total Score' is calculated, its max would be sum of individual maxes if needed
}

# Columns that are scores and should be aggregated
# Excludes 'Daily Total Score' for direct aggregation as it's a sum, but we'll average it.
SCORE_FIELDS_TO_AGGREGATE = list(SCORE_COLUMNS_MAX.keys())

# --- Helper Functions ---

def get_google_sheet_data(service_account_file, sheet_name, tab_name):
    """
    Connects to Google Sheets API and fetches data from the specified tab.
    Returns a pandas DataFrame.
    """
    try:
        scopes = ['https://www.googleapis.com/auth/spreadsheets.readonly']
        creds = Credentials.from_service_account_file(service_account_file, scopes=scopes)
        client = gspread.authorize(creds)
        sheet = client.open(sheet_name).worksheet(tab_name)
        data = sheet.get_all_records() # Fetches data as a list of dictionaries
        df = pd.DataFrame(data)
        print(f"Successfully fetched {len(df)} rows from '{sheet_name} - {tab_name}'.")
        return df
    except Exception as e:
        print(f"Error fetching data from Google Sheets: {e}")
        print("Please ensure:")
        print(f"1. The service account file '{service_account_file}' is correct and accessible.")
        print(f"2. The Google Sheet '{sheet_name}' and tab '{tab_name}' exist.")
        print("3. The service account email has at least 'Viewer' permissions on the sheet.")
        print("4. Google Drive and Google Sheets APIs are enabled in your GCP project.")
        return pd.DataFrame() # Return empty DataFrame on error

def preprocess_data(df):
    """
    Performs necessary data type conversions and cleaning.
    """
    if df.empty:
        return df

    # Convert date column
    if 'Date' in df.columns:
        df['Date'] = pd.to_datetime(df['Date'])
    else:
        print("Error: 'Date' column not found in the DataFrame.")
        return pd.DataFrame() # Or handle error as appropriate

    # Convert score columns to numeric, coercing errors to NaN
    for col in SCORE_FIELDS_TO_AGGREGATE + ['Daily Total Score']: # Include Daily Total for conversion
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        else:
            print(f"Warning: Score column '{col}' not found in DataFrame. Skipping conversion.")

    # Convert Grade to string or category if not already, to avoid issues with it being treated as numeric for grouping
    if 'Grade' in df.columns:
        df['Grade'] = df['Grade'].astype(str)

    # Ensure other key aggregation fields are present
    key_fields = ['Academic Year', 'Week Number', 'Month Number', 'Season-Number', 'Grade', 'Student ID']
    for kf in key_fields:
        if kf not in df.columns:
            print(f"Error: Key field '{kf}' not found in DataFrame. Aggregation may fail.")
            # For now, just print a warning.

    print("\n--- Starting Data Quality Checks ---")
    # --- Data Quality Check 1: Range Checks for Scores ---
    for score_col, max_val in SCORE_COLUMNS_MAX.items():
        if score_col in df.columns:
            # Ensure column is numeric before range check, it should be from earlier conversion
            if pd.api.types.is_numeric_dtype(df[score_col]):
                invalid_scores = df[(df[score_col] < 0) | (df[score_col] > max_val)]
                if not invalid_scores.empty:
                    print(f"DQ Warning: Found {len(invalid_scores)} entries for '{score_col}' outside the expected range (0-{max_val}).")
                    # Example: print(f"  Problematic Record IDs for {score_col}: {invalid_scores['Record ID'].tolist()[:5]}")
            else:
                print(f"DQ Info: Score column '{score_col}' is not numeric, skipping range check.")

    # --- Data Quality Check 2: Missing Value Reporting ---
    print("\nMissing Value Report (for score columns):")
    for col in SCORE_FIELDS_TO_AGGREGATE + ['Daily Total Score']:
        if col in df.columns:
            missing_count = df[col].isnull().sum()
            if missing_count > 0:
                print(f"  Column '{col}': {missing_count} missing (NaN) values out of {len(df)} total rows ({missing_count/len(df)*100:.2f}%).")
        else:
            print(f"  Info: Score column '{col}' not found for missing value check.")

    # --- Data Quality Check 3: Daily Total Score Sanity Check ---
    # This check sums up individual defined scores and compares with 'Daily Total Score'
    # It's a basic check; 'Turn Off Light Score' makes it slightly complex due to conditionality.
    if 'Daily Total Score' in df.columns:
        print("\nDaily Total Score Sanity Check:")
        # Define component scores that ALWAYS contribute (excluding conditional ones like Turn Off Light for this basic sum)
        # For a more accurate check, one might need to adjust 'components_for_sum' based on grade for 'Turn Off Light Score'
        components_for_sum = [sc for sc in SCORE_COLUMNS_MAX.keys() if sc != 'Turn Off Light Score']

        # Calculate sum of defined components, ensuring they exist in df
        actual_component_cols_in_df = [sc for sc in components_for_sum if sc in df.columns]
        df['Computed_Total_From_Components'] = df[actual_component_cols_in_df].sum(axis=1, skipna=True)

        # For rows where Turn Off Light Score applies and is present, add it.
        # This is a simplified approach. A truly robust check would consider the max points for THAT specific record.
        if 'Turn Off Light Score' in df.columns:
             # Add Turn Off Light Score only for applicable grades and if the score is not NaN
            mask_lights_applicable = df['Grade'].isin(['9', '10', '11', '12']) & df['Turn Off Light Score'].notna()
            df.loc[mask_lights_applicable, 'Computed_Total_From_Components'] += df.loc[mask_lights_applicable, 'Turn Off Light Score']

        # Compare, allowing for small floating point differences if necessary, though scores are likely int/whole floats.
        # Only compare where Daily Total Score is not NaN itself.
        comparison_df = df[df['Daily Total Score'].notna()].copy() # Work on a copy for comparison

        try:
            # Ensure both columns are numeric for comparison
            comparison_df['Daily Total Score'] = pd.to_numeric(comparison_df['Daily Total Score'], errors='coerce')
            comparison_df['Computed_Total_From_Components'] = pd.to_numeric(comparison_df['Computed_Total_From_Components'], errors='coerce')

            # Filter out rows where either is now NaN after coercion, to avoid comparison errors
            comparison_df.dropna(subset=['Daily Total Score', 'Computed_Total_From_Components'], inplace=True)

            discrepancies = comparison_df[
                ~comparison_df.apply(lambda row: abs(row['Daily Total Score'] - row['Computed_Total_From_Components']) < 0.01, axis=1)
            ]
            if not discrepancies.empty:
                print(f"  Warning: Found {len(discrepancies)} rows where 'Daily Total Score' significantly differs from the sum of its components.")
                # print(discrepancies[['Record ID', 'Grade', 'Daily Total Score', 'Computed_Total_From_Components']].head())
            else:
                print("  Daily Total Score appears consistent with the sum of its components for checked rows.")
        except Exception as e:
            print(f"  Error during Daily Total Score sanity check: {e}")

        # Clean up the temporary column
        df.drop(columns=['Computed_Total_From_Components'], inplace=True, errors='ignore')
    else:
        print("\nDaily Total Score column not found, skipping sanity check.")

    print("--- Data Quality Checks Complete ---\n")

    # Create output directory if it doesn't exist
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)
        print(f"Created output directory: {OUTPUT_DIR}")

    return df

# --- Main Aggregation Functions (Weekly will be implemented first) ---

def aggregate_to_weekly(df):
    """
    Aggregates data to a weekly level.
    """
    if df.empty or 'Date' not in df.columns:
        print("Cannot perform weekly aggregation: DataFrame is empty or 'Date' column is missing.")
        return pd.DataFrame()

    print("\nStarting Weekly Aggregation...")

    # Define aggregation operations
    # For each score, we want mean, sum, min, max, count
    agg_operations = {}
    for score_col in SCORE_FIELDS_TO_AGGREGATE:
        agg_operations[score_col] = ['mean', 'sum', 'min', 'max', 'count']

    # Also aggregate 'Daily Total Score'
    if 'Daily Total Score' in df.columns:
        agg_operations['Daily Total Score'] = ['mean', 'sum', 'min', 'max']
    else:
        print("Warning: 'Daily Total Score' not found for weekly aggregation.")


    # Group by Academic Year, Week Number, and Grade
    # Ensure 'Week Number' is suitable for direct grouping. ISO week (e.g., 2025W19) should be.
    # If 'Week Number' is just a number (1-53), you might need 'Academic Year' to make it unique.
    # The current data spec has 'Week Number' as 'YYYYWww', which is good.

    # Handle 'Turn Off Light Score' separately due to grade specificity
    # For other scores, aggregate normally

    grouping_cols = ['Academic Year', 'Week Number', 'Grade']
    if not all(col in df.columns for col in grouping_cols):
        print(f"Error: One or more grouping columns ({grouping_cols}) not found. Cannot perform weekly aggregation.")
        return pd.DataFrame()

    # General aggregations (excluding Turn Off Light Score initially)
    general_score_cols = [sc for sc in SCORE_FIELDS_TO_AGGREGATE if sc != 'Turn Off Light Score']
    general_agg_ops = {col: ops for col, ops in agg_operations.items() if col in general_score_cols or col == 'Daily Total Score'}

    if not general_agg_ops:
        print("No general score columns found for aggregation.")
        weekly_agg = pd.DataFrame()
    else:
        weekly_agg = df.groupby(grouping_cols).agg(general_agg_ops)
        weekly_agg.columns = ['_'.join(col).strip() for col in weekly_agg.columns.values] # Flatten MultiIndex columns
        weekly_agg = weekly_agg.reset_index()
        print(f"Aggregated general scores for {len(weekly_agg)} weekly grade groups.")


    # 'Turn Off Light Score' aggregation (Grades 9-12)
    if 'Turn Off Light Score' in df.columns and 'Turn Off Light Score' in agg_operations:
        df_lights = df[df['Grade'].isin(['9', '10', '11', '12'])]
        if not df_lights.empty:
            lights_agg_ops = {'Turn Off Light Score': agg_operations['Turn Off Light Score']}
            weekly_lights_agg = df_lights.groupby(grouping_cols).agg(lights_agg_ops)
            weekly_lights_agg.columns = ['_'.join(col).strip() for col in weekly_lights_agg.columns.values]
            weekly_lights_agg = weekly_lights_agg.reset_index()
            print(f"Aggregated 'Turn Off Light Score' for {len(weekly_lights_agg)} weekly grade groups (Grades 9-12).")

            # Merge light score aggregations back
            if not weekly_agg.empty:
                weekly_agg = pd.merge(weekly_agg, weekly_lights_agg, on=grouping_cols, how='left')
            else: # If only light scores were aggregated (e.g. other scores missing)
                weekly_agg = weekly_lights_agg
        else:
            print("No data for 'Turn Off Light Score' for Grades 9-12.")
    else:
        print("Warning: 'Turn Off Light Score' not found or not in aggregation operations.")

    # Add percentage scores if desired (Example for one score)
    # if 'Stairs Score_mean' in weekly_agg.columns and 'Stairs Score' in SCORE_COLUMNS_MAX:
    #    weekly_agg['Stairs Score_mean_percent'] = \
    #        (weekly_agg['Stairs Score_mean'] / SCORE_COLUMNS_MAX['Stairs Score']) * 100

    # Save to CSV
    if not weekly_agg.empty:
        # Create a filename based on the first academic year and week if possible, or a generic one
        try:
            # Attempt to make a more specific filename if data allows
            sample_year = weekly_agg['Academic Year'].iloc[0] if 'Academic Year' in weekly_agg.columns and not weekly_agg['Academic Year'].empty else "UnknownYear"
            sample_week = weekly_agg['Week Number'].iloc[0] if 'Week Number' in weekly_agg.columns and not weekly_agg['Week Number'].empty else "UnknownWeek"
            # Sanitize filename parts
            sample_year = "".join(c if c.isalnum() or c in ['-'] else "_" for c in str(sample_year))
            sample_week = "".join(c if c.isalnum() or c in ['W', 'w'] else "_" for c in str(sample_week))

            # For weekly, we might want one file per week, or one consolidated file.
            # The plan suggested `weekly_summary_ACADEMICYEAR_Www.csv` which implies one file per period.
            # This example will save one consolidated file for all weeks found in the current batch.
            # To save one file per week, you'd iterate through weekly_agg.groupby(['Academic Year', 'Week Number'])

            filename = os.path.join(OUTPUT_DIR, f"weekly_summary_all_weeks_grades_{datetime.now().strftime('%Y%m%d')}.csv")
            weekly_agg.to_csv(filename, index=False)
            print(f"Successfully saved weekly aggregated data to: {filename}")
        except Exception as e:
            print(f"Error saving weekly aggregated data: {e}")
            # Fallback filename
            filename = os.path.join(OUTPUT_DIR, f"weekly_summary_fallback_{datetime.now().strftime('%Y%m%d%H%M%S')}.csv")
            weekly_agg.to_csv(filename, index=False)
            print(f"Successfully saved weekly aggregated data to fallback: {filename}")

    else:
        print("No data to save for weekly aggregation.")

    return weekly_agg

# --- Placeholder for other aggregation functions ---
def aggregate_to_daily(df):
    """
    Aggregates data to a daily level.
    Saves one CSV file per day found in the input DataFrame.
    """
    if df.empty or 'Date' not in df.columns:
        print("Cannot perform daily aggregation: DataFrame is empty or 'Date' column is missing.")
        return pd.DataFrame()

    print("\nStarting Daily Aggregation...")

    # Define aggregation operations (same as weekly)
    agg_operations = {}
    for score_col in SCORE_FIELDS_TO_AGGREGATE:
        agg_operations[score_col] = ['mean', 'sum', 'min', 'max', 'count']
    if 'Daily Total Score' in df.columns:
        agg_operations['Daily Total Score'] = ['mean', 'sum', 'min', 'max']
    else:
        print("Warning: 'Daily Total Score' not found for daily aggregation.")

    # Grouping columns for daily summary
    # Date itself is the primary unique component for filename, other fields provide context within the file.
    daily_grouping_cols = ['Date', 'Academic Year', 'Month Number', 'Week Number', 'Season-Number', 'Grade']
    if not all(col in df.columns for col in daily_grouping_cols):
        print(f"Error: One or more grouping columns for daily aggregation ({daily_grouping_cols}) not found.")
        return pd.DataFrame()

    all_daily_aggregated_dfs = []

    # Iterate over each day present in the DataFrame to save individual files
    unique_dates = df['Date'].dt.normalize().unique()
    print(f"Found {len(unique_dates)} unique dates for daily aggregation.")

    for specific_date in unique_dates:
        day_df = df[df['Date'].dt.normalize() == specific_date]
        if day_df.empty:
            continue

        date_str = specific_date.strftime('%Y-%m-%d')
        print(f"Processing daily aggregation for {date_str}...")

        # General aggregations (excluding Turn Off Light Score initially)
        general_score_cols = [sc for sc in SCORE_FIELDS_TO_AGGREGATE if sc != 'Turn Off Light Score']
        general_agg_ops = {col: ops for col, ops in agg_operations.items() if col in general_score_cols or col == 'Daily Total Score'}

        daily_agg_for_date = pd.DataFrame()

        if general_agg_ops:
            current_daily_agg = day_df.groupby(daily_grouping_cols).agg(general_agg_ops)
            current_daily_agg.columns = ['_'.join(col).strip() for col in current_daily_agg.columns.values]
            current_daily_agg = current_daily_agg.reset_index()
            daily_agg_for_date = current_daily_agg
            print(f"  Aggregated general scores for {date_str} - {len(daily_agg_for_date)} grade groups.")

        # 'Turn Off Light Score' aggregation (Grades 9-12)
        if 'Turn Off Light Score' in day_df.columns and 'Turn Off Light Score' in agg_operations:
            df_lights = day_df[day_df['Grade'].isin(['9', '10', '11', '12'])]
            if not df_lights.empty:
                lights_agg_ops = {'Turn Off Light Score': agg_operations['Turn Off Light Score']}
                daily_lights_agg = df_lights.groupby(daily_grouping_cols).agg(lights_agg_ops)
                daily_lights_agg.columns = ['_'.join(col).strip() for col in daily_lights_agg.columns.values]
                daily_lights_agg = daily_lights_agg.reset_index()
                print(f"  Aggregated 'Turn Off Light Score' for {date_str} - {len(daily_lights_agg)} grade groups (Grades 9-12).")

                if not daily_agg_for_date.empty:
                    daily_agg_for_date = pd.merge(daily_agg_for_date, daily_lights_agg, on=daily_grouping_cols, how='left')
                else:
                    daily_agg_for_date = daily_lights_agg
            else:
                print(f"  No data for 'Turn Off Light Score' for Grades 9-12 on {date_str}.")
        else:
            print(f"  Warning: 'Turn Off Light Score' not found or not in aggregation operations for {date_str}.")

        if not daily_agg_for_date.empty:
            filename = os.path.join(OUTPUT_DIR, f"daily_summary_{date_str}.csv")
            try:
                daily_agg_for_date.to_csv(filename, index=False)
                print(f"  Successfully saved daily aggregated data to: {filename}")
                all_daily_aggregated_dfs.append(daily_agg_for_date)
            except Exception as e:
                print(f"  Error saving daily aggregated data for {date_str}: {e}")
        else:
            print(f"  No data to save for daily aggregation on {date_str}.")

    if not all_daily_aggregated_dfs:
        print("No daily data was aggregated overall.")
        return pd.DataFrame()

    # Concatenate all daily dataframes into one for returning, though files are saved individually
    return pd.concat(all_daily_aggregated_dfs, ignore_index=True)


def aggregate_to_monthly(df):
    """
    Aggregates data to a monthly level.
    Saves one CSV file per month (per academic year) found in the input DataFrame.
    """
    if df.empty or 'Date' not in df.columns or 'Academic Year' not in df.columns or 'Month Number' not in df.columns:
        print("Cannot perform monthly aggregation: DataFrame is empty or key columns ('Date', 'Academic Year', 'Month Number') are missing.")
        return pd.DataFrame()

    print("\nStarting Monthly Aggregation...")

    # Define aggregation operations (same as weekly/daily)
    agg_operations = {}
    for score_col in SCORE_FIELDS_TO_AGGREGATE:
        agg_operations[score_col] = ['mean', 'sum', 'min', 'max', 'count']
    if 'Daily Total Score' in df.columns:
        agg_operations['Daily Total Score'] = ['mean', 'sum', 'min', 'max']
    else:
        print("Warning: 'Daily Total Score' not found for monthly aggregation.")

    # Grouping columns for monthly summary
    monthly_grouping_cols = ['Academic Year', 'Month Number', 'Grade']
    if not all(col in df.columns for col in monthly_grouping_cols):
        print(f"Error: One or more grouping columns for monthly aggregation ({monthly_grouping_cols}) not found.")
        return pd.DataFrame()

    all_monthly_aggregated_dfs = []

    # Iterate over each unique Academic Year / Month Number combination
    # Ensure 'Month Number' is suitable for grouping, e.g., '01', '02', ..., '12'
    # The raw data spec has 'Month Number' as 'MM', which is good.

    # Create a unique month identifier for iteration (e.g., '2025-2026_09')
    # This assumes 'Month Number' is already padded with zero if needed (e.g. 09 not 9)
    # If 'Month Number' is integer, convert to string and zfill(2)
    df['YearMonth'] = df['Academic Year'].astype(str) + "_" + df['Month Number'].astype(str).str.zfill(2)
    unique_year_months = df['YearMonth'].unique()

    print(f"Found {len(unique_year_months)} unique year-month combinations for monthly aggregation.")

    for ym_combo in unique_year_months:
        month_df = df[df['YearMonth'] == ym_combo]
        if month_df.empty:
            continue

        # Extract year and month for filename and logging
        # This assumes Academic Year does not contain '_'
        current_academic_year, current_month_num = ym_combo.split('_')

        print(f"Processing monthly aggregation for {current_academic_year}, Month: {current_month_num}...")

        # General aggregations
        general_score_cols = [sc for sc in SCORE_FIELDS_TO_AGGREGATE if sc != 'Turn Off Light Score']
        general_agg_ops = {col: ops for col, ops in agg_operations.items() if col in general_score_cols or col == 'Daily Total Score'}

        monthly_agg_for_period = pd.DataFrame()

        if general_agg_ops:
            current_monthly_agg = month_df.groupby(monthly_grouping_cols).agg(general_agg_ops)
            current_monthly_agg.columns = ['_'.join(col).strip() for col in current_monthly_agg.columns.values]
            current_monthly_agg = current_monthly_agg.reset_index()
            monthly_agg_for_period = current_monthly_agg
            print(f"  Aggregated general scores for {ym_combo} - {len(monthly_agg_for_period)} grade groups.")

        # 'Turn Off Light Score' aggregation
        if 'Turn Off Light Score' in month_df.columns and 'Turn Off Light Score' in agg_operations:
            df_lights = month_df[month_df['Grade'].isin(['9', '10', '11', '12'])]
            if not df_lights.empty:
                lights_agg_ops = {'Turn Off Light Score': agg_operations['Turn Off Light Score']}
                monthly_lights_agg = df_lights.groupby(monthly_grouping_cols).agg(lights_agg_ops)
                monthly_lights_agg.columns = ['_'.join(col).strip() for col in monthly_lights_agg.columns.values]
                monthly_lights_agg = monthly_lights_agg.reset_index()
                print(f"  Aggregated 'Turn Off Light Score' for {ym_combo} - {len(monthly_lights_agg)} grade groups.")

                if not monthly_agg_for_period.empty:
                    monthly_agg_for_period = pd.merge(monthly_agg_for_period, monthly_lights_agg, on=monthly_grouping_cols, how='left')
                else:
                    monthly_agg_for_period = monthly_lights_agg
            else:
                print(f"  No data for 'Turn Off Light Score' for Grades 9-12 in {ym_combo}.")
        else:
            print(f"  Warning: 'Turn Off Light Score' not found or not in agg ops for {ym_combo}.")

        if not monthly_agg_for_period.empty:
            # Sanitize academic year for filename
            safe_academic_year = "".join(c if c.isalnum() else "_" for c in str(current_academic_year))
            filename = os.path.join(OUTPUT_DIR, f"monthly_summary_{safe_academic_year}_{current_month_num}.csv")
            try:
                monthly_agg_for_period.to_csv(filename, index=False)
                print(f"  Successfully saved monthly aggregated data to: {filename}")
                all_monthly_aggregated_dfs.append(monthly_agg_for_period)
            except Exception as e:
                print(f"  Error saving monthly aggregated data for {ym_combo}: {e}")
        else:
            print(f"  No data to save for monthly aggregation for {ym_combo}.")

    if not all_monthly_aggregated_dfs:
        print("No monthly data was aggregated overall.")
        return pd.DataFrame()

    return pd.concat(all_monthly_aggregated_dfs, ignore_index=True)


# --- Season Mapping Helper ---
def map_month_to_season(month_number):
    """Maps month number (1-12) to season (S1, S2, S3)."""
    if month_number in [9, 10, 11, 12]: # Sep, Oct, Nov, Dec
        return 'S1'
    elif month_number in [1, 2, 3]:   # Jan, Feb, Mar
        return 'S2'
    elif month_number in [4, 5, 6]:   # Apr, May, Jun
        return 'S3'
    else: # Jul, Aug or other invalid months for these seasons
        return 'UnknownSeason'

def aggregate_to_seasonal(df):
    """
    Aggregates data to a seasonal level.
    Saves one CSV file per season (per academic year) found.
    """
    if df.empty or 'Date' not in df.columns or 'Academic Year' not in df.columns:
        print("Cannot perform seasonal aggregation: DataFrame is empty or key columns ('Date', 'Academic Year') are missing.")
        return pd.DataFrame()

    print("\nStarting Seasonal Aggregation...")

    # Ensure 'Month Number' is present for mapping, or derive it from 'Date'
    if 'Month Number' not in df.columns and 'Date' in df.columns:
        df['Month Number Derived'] = df['Date'].dt.month # Integer month number
    elif 'Month Number' in df.columns: # If 'Month Number' is 'MM' string
         df['Month Number Derived'] = pd.to_numeric(df['Month Number'], errors='coerce')
    else:
        print("Error: Cannot determine month for seasonal mapping. 'Date' or 'Month Number' column required.")
        return pd.DataFrame()

    # Apply season mapping
    # The user's raw data has 'Season-Number'. We should use our mapping as the source of truth
    # or at least validate against it. For now, we will overwrite/create 'Computed Season'.
    df['Computed_Season'] = df['Month Number Derived'].apply(map_month_to_season)

    # Filter out any rows that didn't map to a valid season for this aggregation
    df_valid_seasons = df[df['Computed_Season'] != 'UnknownSeason'].copy()
    if df_valid_seasons.empty:
        print("No data found for the defined seasons (S1, S2, S3).")
        return pd.DataFrame()

    # Define aggregation operations
    agg_operations = {}
    for score_col in SCORE_FIELDS_TO_AGGREGATE:
        agg_operations[score_col] = ['mean', 'sum', 'min', 'max', 'count']
    if 'Daily Total Score' in df.columns:
        agg_operations['Daily Total Score'] = ['mean', 'sum', 'min', 'max']

    # Grouping columns for seasonal summary
    seasonal_grouping_cols = ['Academic Year', 'Computed_Season', 'Grade']
    # Ensure all grouping columns are present in df_valid_seasons
    if not all(col in df_valid_seasons.columns for col in seasonal_grouping_cols):
        print(f"Error: One or more grouping columns for seasonal aggregation ({seasonal_grouping_cols}) not found.")
        return pd.DataFrame()

    all_seasonal_aggregated_dfs = []

    # Create a unique season identifier for iteration (e.g., '2025-2026_S1')
    df_valid_seasons['YearSeason'] = df_valid_seasons['Academic Year'].astype(str) + "_" + df_valid_seasons['Computed_Season']
    unique_year_seasons = df_valid_seasons['YearSeason'].unique()

    print(f"Found {len(unique_year_seasons)} unique year-season combinations for aggregation.")

    for ys_combo in unique_year_seasons:
        season_df = df_valid_seasons[df_valid_seasons['YearSeason'] == ys_combo]
        if season_df.empty:
            continue

        current_academic_year, current_season = ys_combo.split('_')
        print(f"Processing seasonal aggregation for {current_academic_year}, Season: {current_season}...")

        # General aggregations
        general_score_cols = [sc for sc in SCORE_FIELDS_TO_AGGREGATE if sc != 'Turn Off Light Score']
        general_agg_ops = {col: ops for col, ops in agg_operations.items() if col in general_score_cols or col == 'Daily Total Score'}

        seasonal_agg_for_period = pd.DataFrame()

        if general_agg_ops:
            current_seasonal_agg = season_df.groupby(seasonal_grouping_cols).agg(general_agg_ops)
            current_seasonal_agg.columns = ['_'.join(col).strip() for col in current_seasonal_agg.columns.values]
            current_seasonal_agg = current_seasonal_agg.reset_index()
            seasonal_agg_for_period = current_seasonal_agg
            print(f"  Aggregated general scores for {ys_combo} - {len(seasonal_agg_for_period)} grade groups.")

        # 'Turn Off Light Score' aggregation
        if 'Turn Off Light Score' in season_df.columns and 'Turn Off Light Score' in agg_operations:
            df_lights = season_df[season_df['Grade'].isin(['9', '10', '11', '12'])]
            if not df_lights.empty:
                lights_agg_ops = {'Turn Off Light Score': agg_operations['Turn Off Light Score']}
                seasonal_lights_agg = df_lights.groupby(seasonal_grouping_cols).agg(lights_agg_ops)
                seasonal_lights_agg.columns = ['_'.join(col).strip() for col in seasonal_lights_agg.columns.values]
                seasonal_lights_agg = seasonal_lights_agg.reset_index()
                print(f"  Aggregated 'Turn Off Light Score' for {ys_combo} - {len(seasonal_lights_agg)} grade groups.")

                if not seasonal_agg_for_period.empty:
                    seasonal_agg_for_period = pd.merge(seasonal_agg_for_period, seasonal_lights_agg, on=seasonal_grouping_cols, how='left')
                else:
                    seasonal_agg_for_period = seasonal_lights_agg
            else:
                print(f"  No data for 'Turn Off Light Score' for Grades 9-12 in {ys_combo}.")
        else:
            print(f"  Warning: 'Turn Off Light Score' not found or not in agg ops for {ys_combo}.")

        if not seasonal_agg_for_period.empty:
            safe_academic_year = "".join(c if c.isalnum() else "_" for c in str(current_academic_year))
            filename = os.path.join(OUTPUT_DIR, f"seasonal_summary_{safe_academic_year}_{current_season}.csv")
            try:
                seasonal_agg_for_period.to_csv(filename, index=False)
                print(f"  Successfully saved seasonal aggregated data to: {filename}")
                all_seasonal_aggregated_dfs.append(seasonal_agg_for_period)
            except Exception as e:
                print(f"  Error saving seasonal aggregated data for {ys_combo}: {e}")
        else:
            print(f"  No data to save for seasonal aggregation for {ys_combo}.")

    if not all_seasonal_aggregated_dfs:
        print("No seasonal data was aggregated overall.")
        return pd.DataFrame()

    return pd.concat(all_seasonal_aggregated_dfs, ignore_index=True)


def aggregate_to_annual(df):
    """
    Aggregates data to an annual level (by Academic Year).
    Saves one CSV file per academic year found.
    """
    if df.empty or 'Academic Year' not in df.columns or 'Grade' not in df.columns:
        print("Cannot perform annual aggregation: DataFrame is empty or key columns ('Academic Year', 'Grade') are missing.")
        return pd.DataFrame()

    print("\nStarting Annual Aggregation...")

    # Define aggregation operations
    agg_operations = {}
    for score_col in SCORE_FIELDS_TO_AGGREGATE:
        agg_operations[score_col] = ['mean', 'sum', 'min', 'max', 'count']
    if 'Daily Total Score' in df.columns:
        agg_operations['Daily Total Score'] = ['mean', 'sum', 'min', 'max']

    # Grouping columns for annual summary
    annual_grouping_cols = ['Academic Year', 'Grade']

    all_annual_aggregated_dfs = []

    unique_academic_years = df['Academic Year'].unique()
    print(f"Found {len(unique_academic_years)} unique academic years for annual aggregation.")

    for year in unique_academic_years:
        year_df = df[df['Academic Year'] == year]
        if year_df.empty:
            continue

        print(f"Processing annual aggregation for Academic Year: {year}...")

        # General aggregations
        general_score_cols = [sc for sc in SCORE_FIELDS_TO_AGGREGATE if sc != 'Turn Off Light Score']
        general_agg_ops = {col: ops for col, ops in agg_operations.items() if col in general_score_cols or col == 'Daily Total Score'}

        annual_agg_for_year = pd.DataFrame()

        if general_agg_ops:
            current_annual_agg = year_df.groupby(annual_grouping_cols).agg(general_agg_ops)
            current_annual_agg.columns = ['_'.join(col).strip() for col in current_annual_agg.columns.values]
            current_annual_agg = current_annual_agg.reset_index()
            annual_agg_for_year = current_annual_agg
            print(f"  Aggregated general scores for {year} - {len(annual_agg_for_year)} grade groups.")

        # 'Turn Off Light Score' aggregation
        if 'Turn Off Light Score' in year_df.columns and 'Turn Off Light Score' in agg_operations:
            df_lights = year_df[year_df['Grade'].isin(['9', '10', '11', '12'])]
            if not df_lights.empty:
                lights_agg_ops = {'Turn Off Light Score': agg_operations['Turn Off Light Score']}
                annual_lights_agg = df_lights.groupby(annual_grouping_cols).agg(lights_agg_ops)
                annual_lights_agg.columns = ['_'.join(col).strip() for col in annual_lights_agg.columns.values]
                annual_lights_agg = annual_lights_agg.reset_index()
                print(f"  Aggregated 'Turn Off Light Score' for {year} - {len(annual_lights_agg)} grade groups.")

                if not annual_agg_for_year.empty:
                    annual_agg_for_year = pd.merge(annual_agg_for_year, annual_lights_agg, on=annual_grouping_cols, how='left')
                else:
                    annual_agg_for_year = annual_lights_agg
            else:
                print(f"  No data for 'Turn Off Light Score' for Grades 9-12 in {year}.")
        else:
            print(f"  Warning: 'Turn Off Light Score' not found or not in agg ops for {year}.")

        if not annual_agg_for_year.empty:
            safe_academic_year = "".join(c if c.isalnum() else "_" for c in str(year))
            filename = os.path.join(OUTPUT_DIR, f"annual_summary_{safe_academic_year}.csv")
            try:
                annual_agg_for_year.to_csv(filename, index=False)
                print(f"  Successfully saved annual aggregated data to: {filename}")
                all_annual_aggregated_dfs.append(annual_agg_for_year)
            except Exception as e:
                print(f"  Error saving annual aggregated data for {year}: {e}")
        else:
            print(f"  No data to save for annual aggregation for {year}.")

    if not all_annual_aggregated_dfs:
        print("No annual data was aggregated overall.")
        return pd.DataFrame()

    return pd.concat(all_annual_aggregated_dfs, ignore_index=True)


# --- Main Execution ---
if __name__ == '__main__':
    print("Starting Dorm Score Aggregation Process...")

    # 1. Get Data
    raw_df = get_google_sheet_data(SERVICE_ACCOUNT_FILE, GOOGLE_SHEET_NAME, DATA_SHEET_TAB_NAME)

    if raw_df.empty:
        print("Exiting script as no data could be fetched or processed.")
    else:
        # 2. Preprocess Data
        processed_df = preprocess_data(raw_df.copy()) # Use .copy() to avoid modifying original df

        if processed_df.empty:
            print("Exiting script due to preprocessing errors.")
        else:
            # 3. Perform Aggregations (starting with weekly)
            # In a full script, you might call these based on user input or configuration

            daily_aggregated_df = aggregate_to_daily(processed_df.copy())
            weekly_aggregated_df = aggregate_to_weekly(processed_df.copy())
            monthly_aggregated_df = aggregate_to_monthly(processed_df.copy())
            seasonal_aggregated_df = aggregate_to_seasonal(processed_df.copy())
            annual_aggregated_df = aggregate_to_annual(processed_df.copy())

            print("\nAggregation process complete.")
            if not daily_aggregated_df.empty:
                print("\nSample of Daily Aggregated Data (concatenated from all saved daily files):")
                print(daily_aggregated_df.head())
            if not weekly_aggregated_df.empty:
                print("\nSample of Weekly Aggregated Data:")
                print(weekly_aggregated_df.head())
            if not monthly_aggregated_df.empty:
                print("\nSample of Monthly Aggregated Data (concatenated):")
                print(monthly_aggregated_df.head())
            if not seasonal_aggregated_df.empty:
                print("\nSample of Seasonal Aggregated Data (concatenated):")
                print(seasonal_aggregated_df.head())
            if not annual_aggregated_df.empty:
                print("\nSample of Annual Aggregated Data (concatenated):")
                print(annual_aggregated_df.head())

            print(f"\nAll generated CSV files will be in the '{OUTPUT_DIR}' directory.")
            print("Please remember to replace 'path/to/your/service_account_key.json' and other placeholders.")

# End of dorm_score_aggregator.py
