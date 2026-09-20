from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeSerializer


class CalendarRepository:
    def __init__(self, table, transaction_client=None):
        self.table = table
        self.transaction_client = transaction_client

    def get_date(self, day):
        return self.table.get_item(
            Key={"month": day[:7], "date": day}, ConsistentRead=True,
        ).get("Item")

    def swap_assignments(self, my_item, target_item):
        serializer = TypeSerializer()
        writes = []
        for current, other in ((my_item, target_item), (target_item, my_item)):
            names = {"#id": "assignedUserId", "#name": "assignedUserName", "#holiday": "holiday"}
            values = {":oldId": current["assignedUserId"], ":newId": other["assignedUserId"],
                      ":newName": other.get("assignedUserName")}
            conditions = ["#id = :oldId"]
            for field, alias, placeholder in (("assignedUserName", "#name", ":oldName"),
                                               ("holiday", "#holiday", ":oldHoliday")):
                if field in current:
                    conditions.append(f"{alias} = {placeholder}")
                    values[placeholder] = current[field]
                else:
                    conditions.append(f"attribute_not_exists({alias})")
            writes.append({"Update": {
                "TableName": self.table.name,
                "Key": {key: serializer.serialize(current[key]) for key in ("month", "date")},
                "UpdateExpression": "SET #id = :newId, #name = :newName",
                "ConditionExpression": " AND ".join(conditions),
                "ExpressionAttributeNames": names,
                "ExpressionAttributeValues": {key: serializer.serialize(value) for key, value in values.items()},
            }})
        self.transaction_client.transact_write_items(TransactItems=writes)

    def list_month(self, month):
        params = {"KeyConditionExpression": Key("month").eq(month)}
        items = []
        while True:
            page = self.table.query(**params)
            items.extend(page.get("Items", []))
            if not page.get("LastEvaluatedKey"):
                return items
            params["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    def month_exists(self, month):
        result = self.table.query(
            KeyConditionExpression=Key("month").eq(month),
            Select="COUNT",
            Limit=1,
        )
        return result.get("Count", 0) > 0
