import boto3

INSTANCE_ID = "i-08214cf66cd3f80c7"

def handler(event, context):
    ec2 = boto3.client("ec2", region_name="us-east-1")
    state = ec2.describe_instances(InstanceIds=[INSTANCE_ID])["Reservations"][0]["Instances"][0]["State"]["Name"]
    if state == "running":
        ec2.stop_instances(InstanceIds=[INSTANCE_ID])
        msg = f"Circuit breaker: stopped {INSTANCE_ID} (was {state})"
    else:
        msg = f"Circuit breaker: {INSTANCE_ID} already {state}"
    sns = boto3.client("sns", region_name="us-east-1")
    sns.publish(TopicArn="arn:aws:sns:us-east-1:302154194530:jcode-guard-warn",
                Subject="jcode circuit breaker fired", Message=msg)
    return {"message": msg}
