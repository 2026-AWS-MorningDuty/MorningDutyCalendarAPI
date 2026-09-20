from boto3.dynamodb.conditions import Key

from logger import logger
from utils.response import APIError


class UserRepository:
    def __init__(self, table):
        self.table = table

    def find_by_user_id(self, user_id):
        return self.table.get_item(
            Key={"userId": user_id}, ConsistentRead=True,
        ).get("Item")

    def set_email_notification(self, user_id, enabled):
        self.table.update_item(
            Key={"userId": user_id},
            UpdateExpression="SET #notification = :enabled",
            ConditionExpression="attribute_exists(#userId)",
            ExpressionAttributeNames={"#notification": "emailNotification", "#userId": "userId"},
            ExpressionAttributeValues={":enabled": enabled},
        )

    def find_by_cognito_sub(self, sub):
        params = {
            "IndexName": "cognitoSub-index",
            "KeyConditionExpression": Key("cognitoSub").eq(sub),
        }
        users = []
        while True:
            page = self.table.query(**params)
            users.extend(page.get("Items", []))
            if len(users) > 1:
                logger.error("event=user_integrity_failed result=failure reason=duplicate_cognitoSub")
                raise APIError(500, "INTERNAL_ERROR", "User data integrity error.")
            if not page.get("LastEvaluatedKey"):
                return users[0] if users else None
            params["ExclusiveStartKey"] = page["LastEvaluatedKey"]
