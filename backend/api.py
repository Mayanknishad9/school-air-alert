"""
api.py - small HTTP API for the website (AWS Lambda behind API Gateway HTTP API).

  GET  /forecast   -> latest forecast for every area (from S3)
  GET  /stations   -> list of areas a school can pick
  POST /subscribe  -> {"email": "...", "station": "rk_puram"}
                      subscribes a principal to their area's daily alert via SNS
                      (SNS sends a confirmation email first; one click to confirm)
"""
import json
import os
import re

import boto3

from stations import STATIONS

s3 = boto3.client("s3")
sns = boto3.client("sns")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _resp(code, body):
    return {"statusCode": code, "headers": {"Content-Type": "application/json"},
            "body": json.dumps(body, ensure_ascii=False)}


def handler(event, context):
    route = event.get("routeKey", "")
    if route == "GET /forecast":
        try:
            obj = s3.get_object(Bucket=os.environ["FORECAST_BUCKET"], Key="forecasts/latest.json")
        except s3.exceptions.NoSuchKey:
            return _resp(404, {"error": "No forecast yet. The first one is issued at 6 PM IST."})
        return {"statusCode": 200, "headers": {"Content-Type": "application/json"},
                "body": obj["Body"].read().decode()}

    if route == "GET /stations":
        return _resp(200, [{"id": k, "label": v["label"]} for k, v in STATIONS.items()])

    if route == "POST /subscribe":
        try:
            data = json.loads(event.get("body") or "{}")
        except json.JSONDecodeError:
            return _resp(400, {"error": "Invalid JSON"})
        email, station = (data.get("email") or "").strip(), data.get("station")
        if not EMAIL.match(email):
            return _resp(400, {"error": "Please enter a valid email address."})
        if station not in STATIONS:
            return _resp(400, {"error": "Please pick an area from the list."})
        sns.subscribe(
            TopicArn=os.environ["ALERT_TOPIC_ARN"], Protocol="email", Endpoint=email,
            Attributes={"FilterPolicy": json.dumps({"station": [station]})},
        )
        return _resp(200, {"message": f"Check {email} and click the AWS confirmation link "
                                      f"to start getting alerts for {STATIONS[station]['label']}."})

    return _resp(404, {"error": "Not found"})
