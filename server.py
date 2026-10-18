import json
import requests
import pandas as pd
import re
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
        
    records = []
    for line in orders_res.text.strip().splitlines():
        line = line.strip()
        if line:
            try:
                records.append(json.loads(line))
            except:
                pass
                
    df = pd.DataFrame(records)
    
    # Deduplicate updates
    if "updated_at" in df.columns and "id" in df.columns:
        df["updated_at"] = pd.to_datetime(df["updated_at"], errors="coerce", utc=True)
        df = df.sort_values("updated_at").drop_duplicates(subset=["id"], keep="last")
        
    # Fetch exchange rates
    rates_link = root_data.get("links", {}).get("rates")
    rates = {"USD": 1.0}
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
                rates.update({str(k).upper(): float(v) for k, v in rates_json.items()})
        except Exception:
            pass
            
    cached_df = df
    cached_rates = rates
    return cached_df, cached_rates

@app.on_event("startup")
def startup_event():
    try:
        load_ledger_data()
        print("Data loaded successfully.")
    except Exception as e:
        print(f"Startup error: {e}")

@app.post("/")
async def answer_question(req: QuestionRequest):
    try:
        df, rates = load_ledger_data()
        q = req.question.lower()
        sub_df = df.copy()
        
        # 1. Filter Status
        if "paid" in q:
            sub_df = sub_df[sub_df["status"].str.lower() == "paid"]
        elif "refund" in q:
            sub_df = sub_df[sub_df["status"].str.lower() == "refunded"]
        elif "void" in q:
            sub_df = sub_df[sub_df["status"].str.lower() == "void"]

        # 2. Filter Region (West, Central, North, South, East)
        regions = ["west", "central", "north", "south", "east"]
        matched_reg = next((r for r in regions if r in q), None)
        if matched_reg:
            sub_df = sub_df[sub_df["region"].str.lower() == matched_reg]

        # 3. Filter Date / Month / Year using created_at
        months = {
            "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
            "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
            "august": 8, "aug": 8, "september": 9, "sep": 9, "october": 10, "oct": 10,
            "november": 11, "nov": 11, "december": 12, "dec": 12
        }
        matched_month = next((m for m in months if m in q), None)
        years = re.findall(r'\b20\d{2}\b', q)
        matched_year = int(years[0]) if years else None
        
        if (matched_month or matched_year) and "created_at" in sub_df.columns:
            dt_series = pd.to_datetime(sub_df["created_at"], errors="coerce", utc=True)
            if matched_month:
                sub_df = sub_df[dt_series.dt.month == months[matched_month]]
            if matched_year:
                sub_df = sub_df[dt_series.dt.year == matched_year]

        # 4. Filter Product (Exact or partial match on product column)
        if "product" in sub_df.columns:
            unique_procs = sub_df["product"].dropna().unique()
            matched_prod = None
            for p in unique_procs:
                p_lower = str(p).lower()
                if p_lower in q or any(w in q for w in p_lower.split() if len(w) > 2):
                    matched_prod = p
                    break
            if matched_prod is not None:
                sub_df = sub_df[sub_df["product"] == matched_prod]

        # 5. Currency Conversion to USD for amounts
        if "amount" in sub_df.columns and "currency" in sub_df.columns:
            sub_df["amount"] = pd.to_numeric(sub_df["amount"], errors="coerce")
            def to_usd(row):
                cur = str(row["currency"]).upper()
                amt = row["amount"]
                if pd.isna(amt):
                    return 0.0
                if cur == "USD":
                    return amt
                rate = rates.get(cur, 1.0)
                return amt * rate
            sub_df["usd_amount"] = sub_df.apply(to_usd, axis=1)
            calc_col = "usd_amount"
        else:
            calc_col = "amount" if "amount" in sub_df.columns else None

        # Safety Fallback
        if sub_df.empty:
            sub_df = df.copy()
            if "amount" in sub_df.columns and "currency" in sub_df.columns:
                sub_df["usd_amount"] = sub_df.apply(lambda r: r["amount"] if str(r["currency"]).upper()=="USD" else r["amount"]*rates.get(str(r["currency"]).upper(), 1.0), axis=1)
                calc_col = "usd_amount"

        # 6. Question Intent Handlers
        if "average" in q or "avg" in q or "mean" in q:
            if calc_col:
                return {"answer": round(float(sub_df[calc_col].mean()), 2)}
            return {"answer": 0.0}

        if "distinct" in q or "customer" in q or "buyer" in q:
            return {"answer": int(sub_df["customer"].nunique())}

        if "revenue" in q or "total" in q or "sales" in q or "sum" in q:
            if calc_col:
                return {"answer": round(float(sub_df[calc_col].sum()), 2)}
            return {"answer": 0.0}

        return {"answer": len(sub_df)}

    except Exception as e:
        print(f"Error handling query: {e}")
        return {"answer": 0}