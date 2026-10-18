import requests
import pandas as pd
import json

EMAIL = "24f2006942@ds.study.iitm.ac.in"
QUIZ_SIGN = "u4Rf/VHuXKZzfboOlrvTEeDTWVnRDaIUsuIxjUO44Og+ZjetWp1hG5wlWtYuLmuBAvyvviBwblPnQLgr3PaYVPsdDLJbptVAKzCKAmEC9jWRcv2qfQicAokgUSH0M7KAfij/HJVAex6VvXynO+2UeocnEAtIfGmm6fRqV5y2WbkIUas4sx56P/sOGuFhckv3BH5tFl+Zdbe/YeECY8khdK7xZmWYONiGk6ElsXY9aFdweyq5Uu4tWpqUtBdeBO8ec5PpnFScGh7MYBzXe+DmnMtjNZsT6Owc5gZgepGDDbp68e0N0oqJeePpPhbu9weZqyHaC/qnSgY357qYNLL+Vg=="

res = requests.get("https://exam.sanand.workers.dev/questionData", params={
    "email": EMAIL,
    "quizSign": QUIZ_SIGN,
    "questionId": "q-ledger-agent-server",
    "path": "/"
})
data = res.json()
export_link = data.get("links", {}).get("export")

export_res = requests.get(export_link)
records = []
for line in export_res.text.strip().splitlines():
    line = line.strip()
    if line:
        try:
            records.append(json.loads(line))
        except:
            pass

df = pd.DataFrame(records)

print("COLUMNS:", df.columns.tolist())
print("\nFIRST ROW:\n", df.iloc[0].to_dict())
if "status" in df.columns:
    print("\nUNIQUE STATUSES:", df["status"].unique())
if "region" in df.columns:
    print("\nUNIQUE REGIONS:", df["region"].unique())
if "currency" in df.columns:
    print("\nUNIQUE CURRENCIES:", df["currency"].unique())