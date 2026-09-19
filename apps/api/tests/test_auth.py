async def test_login_succeeds_with_correct_credentials(client, test_user):
    response = await client.post(
        "/api/auth/login",
        json={"email": test_user.email, "password": "correct-horse-battery"},
    )
    assert response.status_code == 200
    assert response.json()["email"] == test_user.email
    assert "truffler_session" in response.cookies


async def test_login_fails_with_wrong_password(client, test_user):
    response = await client.post(
        "/api/auth/login",
        json={"email": test_user.email, "password": "wrong-password"},
    )
    assert response.status_code == 401
    assert "truffler_session" not in response.cookies


async def test_login_fails_for_unknown_user(client):
    response = await client.post(
        "/api/auth/login",
        json={"email": "nobody@example.com", "password": "whatever123"},
    )
    assert response.status_code == 401


async def test_me_requires_authentication(client):
    response = await client.get("/api/me")
    assert response.status_code == 401


async def test_me_returns_current_user_after_login(client, test_user):
    login = await client.post(
        "/api/auth/login",
        json={"email": test_user.email, "password": "correct-horse-battery"},
    )
    assert login.status_code == 200

    response = await client.get("/api/me")
    assert response.status_code == 200
    assert response.json()["email"] == test_user.email


async def test_logout_clears_session(client, test_user):
    await client.post(
        "/api/auth/login",
        json={"email": test_user.email, "password": "correct-horse-battery"},
    )
    logout = await client.post("/api/auth/logout")
    assert logout.status_code == 204

    response = await client.get("/api/me")
    assert response.status_code == 401


async def test_protected_route_rejects_tampered_cookie(client):
    client.cookies.set("truffler_session", "not-a-valid-token")
    response = await client.get("/api/me")
    assert response.status_code == 401
