import pandas as pd
import requests
from datetime import datetime, timedelta
import time
from tqdm import tqdm
import json
import os
from collections import defaultdict
import pickle
from ratelimit import limits, sleep_and_retry

# Constants
CALLS_PER_SECOND = 250
ONE_SECOND = 1
CHECKPOINT_FILE = 'search_checkpoint.pkl'
TEMP_RESULTS_FILE = 'temp_results.pkl'
ERROR_LOG_FILE = 'error_log.txt'

class SearchState:
    def __init__(self, total_rows):
        self.processed_rows = set()
        self.current_results = []
        self.total_rows = total_rows
        self.error_counts = defaultdict(int)
        
    def save_checkpoint(self):
        with open(CHECKPOINT_FILE, 'wb') as f:
            pickle.dump({
                'processed_rows': self.processed_rows,
                'error_counts': dict(self.error_counts)
            }, f)
        
        with open(TEMP_RESULTS_FILE, 'wb') as f:
            pickle.dump(self.current_results, f)
    
    def load_checkpoint(self):
        if os.path.exists(CHECKPOINT_FILE) and os.path.exists(TEMP_RESULTS_FILE):
            with open(CHECKPOINT_FILE, 'rb') as f:
                checkpoint_data = pickle.load(f)
                self.processed_rows = checkpoint_data['processed_rows']
                self.error_counts = defaultdict(int, checkpoint_data['error_counts'])
            
            with open(TEMP_RESULTS_FILE, 'rb') as f:
                self.current_results = pickle.load(f)
            return True
        return False

def log_error(error_msg):
    """Log errors with timestamp"""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(ERROR_LOG_FILE, 'a') as f:
        f.write(f"{timestamp}: {error_msg}\n")

@sleep_and_retry
@limits(calls=CALLS_PER_SECOND, period=ONE_SECOND)
def rate_limited_search(query, start_date, end_date, subscription_key, endpoint):
    """Rate-limited version of the Bing search function"""
    headers = {'Ocp-Apim-Subscription-Key': subscription_key}
    params = {
        'q': query,
        'count': 20,
        'freshness': f'{start_date}..{end_date}',
        'responseFilter': 'Webpages'
    }
    
    try:
        response = requests.get(endpoint, headers=headers, params=params)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        if response.status_code == 429:  # Rate limit exceeded
            retry_after = int(response.headers.get('Retry-After', 60))
            time.sleep(retry_after)
            return rate_limited_search(query, start_date, end_date, subscription_key, endpoint)
        raise e

def generate_search_queries(row):
    """Generate all search patterns for a given row"""
    state = get_state_name(row['ST'])
    county = row['CountyName']
    
    return {
        'pattern1': f"Immigration raid {county}, {state}",
        'pattern2': f"ICE arrests {county} {state}",
        'pattern3': f"Immigration arrest {state}",
        'pattern4': f"Immigration raid {state}"
    }

def process_search_results(search_results, row_data, query, pattern):
    """Process search results with error handling"""
    results = []
    try:
        if search_results and 'webPages' in search_results and 'value' in search_results['webPages']:
            for item in search_results['webPages']['value']:
                results.append({
                    'query': query,
                    'search_pattern': pattern,
                    'StateCountyFIPS': row_data['StateCountyFIPS'],
                    'ST': row_data['ST'],
                    'CountyName': row_data['CountyName'],
                    'FIPSState': row_data['FIPSState'],
                    'FIPSCounty': row_data['FIPSCounty'],
                    'arrest_date': row_data['arrestdate'],
                    'title': item.get('name', ''),
                    'url': item.get('url', ''),
                    'date_published': item.get('dateLastCrawled', '')
                })
    except Exception as e:
        log_error(f"Error processing results: {str(e)}")
    return results

def process_csv_and_search(csv_path, subscription_key, endpoint, batch_size=100):
    """Process CSV in batches with error recovery"""
    # Read CSV
    df = pd.read_csv(csv_path)
    
    # Initialize or load state
    state = SearchState(len(df))
    if state.load_checkpoint():
        print(f"Resuming from checkpoint. {len(state.processed_rows)} rows already processed.")
    
    try:
        # Process in batches
        for start_idx in tqdm(range(0, len(df), batch_size)):
            batch_df = df.iloc[start_idx:start_idx + batch_size]
            
            for _, row in batch_df.iterrows():
                row_id = row.name
                
                # Skip if already processed
                if row_id in state.processed_rows:
                    continue
                
                try:
                    # Generate queries for all patterns
                    queries = generate_search_queries(row)
                    
                    # Calculate date range
                    start_date = format_date(pd.to_datetime(row['arrestdate']) + timedelta(days=-1))
                    end_date = format_date(pd.to_datetime(row['arrestdate']) + timedelta(days=14))
                    
                    # Perform searches for each pattern
                    for pattern, query in queries.items():
                        try:
                            search_results = rate_limited_search(
                                query, start_date, end_date, 
                                subscription_key, endpoint
                            )
                            results = process_search_results(search_results, row, query, pattern)
                            state.current_results.extend(results)
                        except Exception as e:
                            error_msg = f"Error in search pattern {pattern} for row {row_id}: {str(e)}"
                            log_error(error_msg)
                            state.error_counts[str(e)] += 1
                    
                    state.processed_rows.add(row_id)
                    
                except Exception as e:
                    error_msg = f"Error processing row {row_id}: {str(e)}"
                    log_error(error_msg)
                    state.error_counts[str(e)] += 1
                
                # Save checkpoint every 100 rows
                if len(state.processed_rows) % 100 == 0:
                    state.save_checkpoint()
                    print(f"\nCheckpoint saved. Processed {len(state.processed_rows)} rows.")
                    print(f"Current error counts: {dict(state.error_counts)}")
    
    except KeyboardInterrupt:
        print("\nProcess interrupted by user. Saving progress...")
        state.save_checkpoint()
        raise
    
    except Exception as e:
        print(f"\nUnexpected error: {str(e)}. Saving progress...")
        state.save_checkpoint()
        raise
    
    finally:
        # Save final results
        results_df = pd.DataFrame(state.current_results)
        if not results_df.empty:
            results_df.to_csv('final_search_results.csv', index=False)
        
        # Save error summary
        with open('error_summary.json', 'w') as f:
            json.dump(dict(state.error_counts), f, indent=2)
    
    return results_df

def main():
    # File paths and configuration
    full_dataset_path = './Datasets/Abnormal Arrest Days, All Days All Counties.xlsx - Sheet1.csv'
    
    # Read API key
    with open('./api_keys/azure_api_key.txt', 'r') as f:
        subscription_key = f.read().strip()
    
    # Check for subscription_key existence
    assert subscription_key
    
    endpoint = "https://api.bing.microsoft.com/v7.0/search"
    
    try:
        # Process and search
        results_df = process_csv_and_search(
            full_dataset_path, 
            subscription_key, 
            endpoint,
            batch_size=100
        )
        
        # Save final results
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        results_df.to_csv(f'search_results_all_patterns_{timestamp}.csv', index=False)
        
        # Clean up checkpoint files
        for file in [CHECKPOINT_FILE, TEMP_RESULTS_FILE]:
            if os.path.exists(file):
                os.remove(file)
        
        return results_df
    
    except Exception as e:
        print(f"Error in main process: {str(e)}")
        print("Check error_log.txt and error_summary.json for details")
        return None

if __name__ == "__main__":
    results_df = main()