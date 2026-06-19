import os
from fastapi import APIRouter


class UserService:
    def get_user(self, user_id: str):
        return load_user(user_id)


def load_user(user_id: str):
    return os.getenv(user_id)


def handler():
    service = UserService()
    return service.get_user("42")
