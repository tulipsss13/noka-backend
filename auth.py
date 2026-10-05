from jose import jwt

SECRET_KEY = "noka-secret-key"
ALGORITHM = "HS256"


def create_access_token(user_id: int):
    data = {
        "user_id": user_id
    }

    token = jwt.encode(
        data,
        SECRET_KEY,
        algorithm=ALGORITHM
    )

    return token

def decode_access_token(token: str):
    data = jwt.decode(
        token,
        SECRET_KEY,
        algorithms=[ALGORITHM]
    )

    return data