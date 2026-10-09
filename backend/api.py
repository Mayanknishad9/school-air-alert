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


def _find_subscription(email):
    """ARN of this email's subscription on the alert topic ('PendingConfirmation' if not
    yet confirmed), or None if it has none."""
    pages = sns.get_paginator("list_subscriptions_by_topic").paginate(
        TopicArn=os.environ["ALERT_TOPIC_ARN"])
    for page in pages:
        for sub in page["Subscriptions"]:
            if sub["Protocol"] == "email" and sub["Endpoint"].lower() == email.lower():
                return sub["SubscriptionArn"]
    return None


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
        label = STATIONS[station]["label"]
        existing = _find_subscription(email)
        if existing is None:
            sns.subscribe(
                TopicArn=os.environ["ALERT_TOPIC_ARN"], Protocol="email", Endpoint=email,
                Attributes={"FilterPolicy": json.dumps({"station": [station]})},
            )
            return _resp(200, {"message": f"Check {email} and click the AWS confirmation link "
                                          f"to start getting alerts for {label}."})
        if existing == "PendingConfirmation":
            # SNS cannot change a subscription until it is confirmed
            return _resp(409, {"error": f"{email} has a confirmation email waiting. Click the "
                                        f"link in it first, then add {label} again."})
        attrs = sns.get_subscription_attributes(SubscriptionArn=existing)["Attributes"]
        areas = json.loads(attrs.get("FilterPolicy") or "{}").get("station", [])
        if station in areas:
            return _resp(200, {"message": f"{email} already gets alerts for {label}."})
        areas.append(station)
        sns.set_subscription_attributes(
            SubscriptionArn=existing, AttributeName="FilterPolicy",
            AttributeValue=json.dumps({"station": areas}),
        )
        names = ", ".join(STATIONS[a]["label"] for a in areas if a in STATIONS)
        return _resp(200, {"message": f"Added. {email} now gets alerts for: {names}."})

    return _resp(404, {"error": "Not found"})
