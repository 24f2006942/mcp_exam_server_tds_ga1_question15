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
cached_rates = None

def load_ledger_data():
    global cached_df, cached_rates
    if cached_df is not None:
        return cached_df, cached_rates
    
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
        orders_res = requests.get(BASE_WORKER_URL, params={"email": EMAIL, "quizSign": QUIZ_SIGN, "questionId": "q-ledger-agent-server", "path": "/orders"})
        
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
    
    if "updated_at" in df.columns and "id" in df.columns:
        df["updated_at"] = pd.to_datetime(df["updated_at"], errors="coerce", utc=True)
        df = df.sort_values("updated_at").drop_duplicates(subset=["id"], keep="last")
        
    # Print columns to local terminal for easy inspection
    print("DataFrame Columns:", df.columns.tolist())
    
    rates_link = root_data.get("links", {}).get("rates")
    rates = {}
    if rates_link:
        try:
            rates_res = requests.get(rates_link)
            rates_json = rates_res.json()
            if isinstance(rates_json, list):
                for r in rates_json:
                    cur = r.get("currency") or r.get("code")
                    val = r.get("rate") or r.get("usd") or r.get("value")
                    if cur and val:
                        rates[str(cur).upper()] = float(val)
            elif isinstance(rates_json, dict):
                rates = {str(k).upper(): float(v) for k, v in rates_json.items()}
        except Exception:
            pass
            
    cached_df = df
    cached_rates = rates
    return cached_df, cached_rates

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
        df, rates = load_ledger_data()
        q = req.question.lower()
        
        sub_df = df.copy()
        
        # 1. Flexible Status filter (e.g. paid)
        if "paid" in q:
            status_cols = [c for c in sub_df.columns if "status" in c.lower() or "state" in c.lower()]
            if status_cols:
                mask = False
                for sc in status_cols:
                    mask = mask | sub_df[sc].astype(str).str.lower().str.contains("paid", na=False)
                sub_df = sub_df[mask]

        # 2. Flexible Region filter (e.g. central, east, west, north, south)
        regions = ["central", "east", "west", "north", "south"]
        matched_reg = next((reg for reg in regions if reg in q), None)
        if matched_reg:
            reg_cols = [c for c in sub_df.columns if "reg" in c.lower() or "zone" in c.lower() or "location" in c.lower() or "territory" in c.lower()]
            if reg_cols:
                mask = False
                for rc in reg_cols:
                    mask = mask | sub_df[rc].astype(str).str.lower().str.contains(matched_reg, na=False)
                sub_df = sub_df[mask]

        # 3. Flexible Product keyword search (e.g. "rice cooker")
        skip_words = {"how", "many", "distinct", "customers", "have", "bought", "a", "the", "paid", "orders", "only", "what", "is", "average", "order", "value", "in", "usd", "for", "central", "region", "total", "revenue", "sales"}
        q_words = [w for w in q.replace("?", "").replace(",", "").split() if w not in skip_words]
        
        if q_words:
            str_cols = sub_df.select_dtypes(include=["object", "string"]).columns
            for qw in q_words:
                if len(qw) > 2: # ignore small helper words
                    mask = False
                    for col in str_cols:
                        mask = mask | sub_df[col].astype(str).str.lower().str.contains(qw, na=False)
                    filtered_test = sub_df[mask]
                    if len(filtered_test) > 0:
                        sub_df = filtered_test

        # 4. Handle Currency Conversion
        val_col = next((c for c in sub_df.columns if any(k in c.lower() for k in ["amount", "total", "price", "revenue", "cost"])), None)
        curr_col = next((c for c in sub_df.columns if "curr" in c.lower()), None)
        
        if val_col and curr_col and rates:
            sub_df[val_col] = pd.to_numeric(sub_df[val_col], errors="coerce")
            def convert_to_usd(row):
                cur = str(row.get(curr_col, "USD")).upper()
                amt = row[val_col]
                if pd.isna(amt):
                    return 0.0
                if cur == "USD":
                    return amt
                rate = rates.get(cur, 1.0)
                return amt * rate
            sub_df["usd_amount"] = sub_df.apply(convert_to_usd, axis=1)
            calc_col = "usd_amount"
        elif val_col:
            sub_df[val_col] = pd.to_numeric(sub_df[val_col], errors="coerce")
            calc_col = val_col
        else:
            calc_col = None

        # 5. Handle Average / Mean
        if "average" in q or "avg" in q or "mean" in q:
            if calc_col:
                return {"answer": round(float(sub_df[calc_col].mean()), 2)}

        # 6. Handle Distinct Customers count
        if "distinct" in q or "customer" in q or "buyer" in q or "user" in q or "client" in q:
            cust_col = next((c for c in sub_df.columns if any(k in c.lower() for k in ["customer", "client", "user", "email", "buyer", "customer_id"]) and c != "id"), None)
            if not cust_col:
                cust_col = next((c for c in sub_df.columns if "id" in c.lower()), None)
            if cust_col:
                return {"answer": int(sub_df[cust_col].nunique())}

        # 7. Handle Revenue / Total sum
        if "revenue" in q or "total" in q or "sales" in q or "sum" in q:
            if calc_col:
                return {"answer": round(float(sub_df[calc_col].sum()), 2)}

        # Default fallback count
        return {"answer": len(sub_df)}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))