import json
import requests
import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()

BASE_WORKER_URL = "https://exam.sanand.workers.dev/questionData"
EMAIL = "24f2006942@ds.study.iitm.ac.in"
QUIZ_SIGN = "u4Rf/VHuXKZzfboOlrvTEeDTWVnRDaIUsuIxjUO44Og+ZjetWp1hG5wlWtYuLmuBAvyvviBwblPnQLgr3PaYVPsdDLJbptVAKzCKAmEC9jWRcv2qfQicAokgUSH0M7KAfij/HJVAex6VvXynO+2UeocnEAtIfGmm6fRqV5y2WbkIUas4sx56P/sOGuFhckv3BH5tFl+Zdbe/YeECY8khdK7xZmWYONiGk6ElsXY9aFdweyq5Uu4tWpqUtBdeBO8ec5PpnFScGh7MYBzXe+DmnMtjNZsT6Owc5gZgepGDDbp68e0N0oqJeePpPhbu9weZqyHaC/qnSgY357qYNLL+Vg=="

class QuestionRequest(BaseModel):
    question: str

cached_df = None

def load_ledger_data():
    global cached_df
    if cached_df is not None:
        return cached_df
    
    base_params = {
        "email": EMAIL,
        "quizSign": QUIZ_SIGN,
        "questionId": "q-ledger-agent-server",
        "path": "/"
    }
    
    res = requests.get(BASE_WORKER_URL, params=base_params)
    root_data = res.json()
    
    export_link = root_data.get("links", {}).get("export")
    if export_link:
        orders_res = requests.get(export_link)
    else:
        orders_params = {
            "email": EMAIL,
            "quizSign": QUIZ_SIGN,
            "questionId": "q-ledger-agent-server",
            "path": "/orders"
        }
        orders_res = requests.get(BASE_WORKER_URL, params=orders_params)
        
    text_content = orders_res.text.strip()
    
    try:
        orders_json = json.loads(text_content)
    except json.JSONDecodeError:
        records = []
        for line in text_content.splitlines():
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except Exception:
                    pass
        orders_json = records

    if isinstance(orders_json, dict):
        records = None
        for k, v in orders_json.items():
            if isinstance(v, list):
                records = v
                break
        if records is None:
            records = [orders_json]
        orders_json = records
                
    df = pd.DataFrame(orders_json)
    
    # Apply deduplication rule: latest updated_at is current for each order id
    if "updated_at" in df.columns and "id" in df.columns:
        df["updated_at"] = pd.to_datetime(df["updated_at"], errors="coerce", utc=True)
        df = df.sort_values("updated_at").drop_duplicates(subset=["id"], keep="last")
        
    cached_df = df
    return cached_df

@app.on_event("startup")
def startup_event():
    try:
        load_ledger_data()
        print("Ledger data loaded and cached successfully.")
    except Exception as e:
        print(f"Error loading ledger data: {e}")

@app.post("/")
async def answer_question(req: QuestionRequest):
    try:
        df = load_ledger_data()
        q = req.question.lower()
        
        # Filter paid orders if specified or implied
        sub_df = df.copy()
        if "paid" in q and "status" in sub_df.columns:
            sub_df = sub_df[sub_df["status"].str.lower() == "paid"]
            
        # Handle Product filtering (e.g. "rice cooker")
        matched_product = None
        # Check column names that might represent product
        prod_col = next((c for c in ["product", "item", "product_name", "name"] if c in sub_df.columns), None)
        if prod_col:
            unique_procs = sub_df[prod_col].dropna().unique()
            for p in unique_procs:
                if str(p).lower() in q:
                    matched_product = p
                    break
            if matched_product is not None:
                sub_df = sub_df[sub_df[prod_col] == matched_product]

        # Handle Distinct Customers count
        if "distinct" in q or "how many" in q and ("customer" in q or "buyer" in q or "user" in q):
            cust_col = next((c for c in ["customer_id", "customer", "email", "user_id"] if c in sub_df.columns), None)
            if cust_col:
                return {"answer": int(sub_df[cust_col].nunique())}

        # Handle Revenue / Total sum
        if "revenue" in q or "total" in q or "sales" in q or "amount" in q:
            val_col = next((c for c in ["amount", "total", "price", "revenue"] if c in sub_df.columns), None)
            if val_col:
                return {"answer": round(float(sub_df[val_col].sum()), 2)}

        # Default count of rows
        return {"answer": len(sub_df)}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))