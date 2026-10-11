"""Replay dead-letter messages onto the tier queues. Prints one count per queue."""

import os

import boto3

from ingestion_worker.replay import replay_all


def main() -> None:
    client = boto3.client(
        "sqs",
        endpoint_url=os.environ["AWS_ENDPOINT_URL"],
        region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY", "test"),
    )
    counts = replay_all(client)
    for name, count in counts.items():
        print(f"{name} {count}")
    print(f"replayed {sum(counts.values())}")


if __name__ == "__main__":
    main()
