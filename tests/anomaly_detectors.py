import polars as pl
import sys
import polars as pl
import glob
import os
script_dir = os.path.dirname(os.path.abspath(__file__))
os.chdir(script_dir)
sys.path.append('..')
import loglead.anomaly_detection as ad

# Set your directory
test_data_path = "/home/mmantyla/Datasets/test_data"  

# Get all .parquet files in the directory
all_files = glob.glob(os.path.join(test_data_path, "*.parquet"))

print ("Anomaly detectors test starting.")

# Extract unique dataset names with '_eh_' , excluding '_seq' files
datasets = set()
for f in all_files:
    basename = os.path.basename(f)
    if "_lo" in basename and "_eh" in basename and "_seq" not in basename:
        dataset_name = basename.replace(".parquet", "")
        datasets.add(dataset_name)

cols_event = ["m_message", "e_words", "e_event_id", "e_trigrams", "e_event_spell_id", "e_event_lenma_id", "e_bert_emb"] 
numeric_cols = ["seq_len", "eve_len_max", "duration_sec", "eve_len_over1"]
for dataset in datasets:
    # Load the event level data
    primary_file = os.path.join(test_data_path, f"{dataset}.parquet")
    print(f"\nLoading {primary_file}")
    df = pl.read_parquet(primary_file)

    for col in cols_event:
          #Only run if the predictor column AND anomaly labels are present AND we have at least two classes in the data
        if col in df.columns and "anomaly" in df.columns and len(df["anomaly"].unique()) > 1:
            print(f"Running event anomaly detectors with {col}")
            sad =  ad.AnomalyDetection(item_list_col=col, print_scores= False, store_scores=True)
            sad.test_train_split (df, test_frac=0.5) 
            sad.evaluate_all_ads(disabled_methods=[])

    seq_file = primary_file.replace(f"{dataset}.parquet", f"{dataset}_seq.parquet")
    if os.path.exists(seq_file):
        df_seq = pl.read_parquet(seq_file)
        print(f"Running seqeuence anomaly detectors with {seq_file}")
        for col in cols_event:
            if col in df_seq.columns and "anomaly" in df_seq.columns and len(df_seq["anomaly"].unique()) > 1:
                print(f"Running seqeuence anomaly detectors with {col}")
                sad =  ad.AnomalyDetection(item_list_col=col, print_scores= False, store_scores=True)
                #High training fraction to ensure we always have suffiecient samples as these are reduced dataframes 
                sad.test_train_split (df_seq, test_frac=0.2) 
                sad.evaluate_all_ads(disabled_methods=[])

print ("Anomaly detectors test complete.")