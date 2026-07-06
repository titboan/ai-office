"""
Regression-тесты на баг из скриншота пользователя: "с Ozon не приходят вопросы".

Реализация была написана по аналогии с другими Ozon-эндпоинтами без проверки
документации (см. ai-clone/feedback/check-api-docs-before-implementing.md —
тот же класс бага уже был у WB questions). Три независимых дефекта:

1. get_questions отправлял {"page", "page_size", "status": "not_answered"} —
   у Ozon /v1/question/list курсорная пагинация last_id и filter.status
   с enum NEW/VIEWED/PROCESSED/UNPROCESSED/ALL, полей page/page_size нет вовсе.
2. Текст вопроса читался из несуществующих полей question_text/question —
   реальное поле в ответе называется "text", поэтому question_text всегда
   был пустым, даже если бы запрос выше сработал.
3. answer_question не отправлял обязательное поле "sku" — Ozon отклоняет
   такой запрос, поэтому одобренный в Telegram ответ не долетал до Ozon.

Источник формата: github.com/salacoste/ozon-daytona-seller-api
src/types/{requests,responses}/questions-answers.ts
"""
from unittest.mock import AsyncMock, patch

from tools.marketplace import OzonClient


def _make_client() -> OzonClient:
    return OzonClient(api_token="token", client_id="123")


async def test_get_questions_sends_filter_status_not_page_page_size():
    fake_response = {
        "questions": [
            {
                "id": "q1",
                "sku": 1033466212,
                "text": "Можно ли щенку с 2.5 месяцев?",
                "published_at": "2025-11-11T10:00:00Z",
                "status": "UNPROCESSED",
            }
        ],
        "last_id": "",
    }
    with patch("tools.marketplace._request", new=AsyncMock(return_value=fake_response)) as mocked:
        results = await _make_client().get_questions()

    sent_body = mocked.call_args.kwargs["json"]
    assert "page" not in sent_body
    assert "page_size" not in sent_body
    assert sent_body["filter"]["status"] == "UNPROCESSED"

    assert len(results) == 1
    assert results[0]["question_id"] == "q1"
    assert results[0]["product_id"] == "1033466212"
    # Регрессия: раньше question_text всегда был "" из-за неверного имени поля
    assert results[0]["question_text"] == "Можно ли щенку с 2.5 месяцев?"
    assert results[0]["created_at"] == "2025-11-11T10:00:00Z"


async def test_get_questions_paginates_with_last_id_cursor():
    page_1 = {"questions": [{"id": "q1", "sku": 1, "text": "a", "published_at": ""}], "last_id": "cursor-1"}
    page_2 = {"questions": [{"id": "q2", "sku": 2, "text": "b", "published_at": ""}], "last_id": ""}
    with patch("tools.marketplace._request", new=AsyncMock(side_effect=[page_1, page_2])) as mocked:
        results = await _make_client().get_questions()

    assert [r["question_id"] for r in results] == ["q1", "q2"]
    # Второй запрос должен передать last_id из первого ответа
    second_call_body = mocked.call_args_list[1].kwargs["json"]
    assert second_call_body["last_id"] == "cursor-1"


async def test_answer_question_includes_required_sku_field():
    with patch("tools.marketplace._request", new=AsyncMock(return_value={"result": "ok"})) as mocked:
        ok = await _make_client().answer_question("q1", "Да, можно.", product_id="1033466212")

    assert ok is True
    sent_body = mocked.call_args.kwargs["json"]
    assert sent_body["sku"] == 1033466212
    assert sent_body["question_id"] == "q1"
    assert sent_body["text"] == "Да, можно."


async def test_answer_question_without_sku_fails_fast_without_http_call():
    with patch("tools.marketplace._request", new=AsyncMock()) as mocked:
        ok = await _make_client().answer_question("q1", "Да, можно.", product_id=None)

    assert ok is False
    mocked.assert_not_called()
