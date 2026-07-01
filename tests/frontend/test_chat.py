"""
聊天功能测试：验证聊天交互、消息发送、流式响应等。
"""

from playwright.sync_api import Page, expect


def test_send_message(authenticated_page: Page):
    """测试发送消息功能。"""
    page = authenticated_page

    # 输入问题
    question_input = page.locator("#questionInput")
    question_input.fill("什么是证券清算？")

    # 点击发送按钮
    send_btn = page.locator("#sendBtn")
    send_btn.click()

    # 验证输入框被清空
    expect(question_input).to_have_value("")

    # 验证消息容器出现
    messages_container = page.locator("#messagesContainer")
    expect(messages_container).to_be_visible()

    # 验证至少有一条消息
    messages = messages_container.locator(".message")
    expect(messages).to_have_count(2)  # 用户消息 + 助手消息


def test_message_display(authenticated_page: Page):
    """测试消息显示格式。"""
    page = authenticated_page

    # 发送消息
    question_input = page.locator("#questionInput")
    question_input.fill("测试消息")
    page.locator("#sendBtn").click()

    # 等待消息渲染
    page.wait_for_timeout(500)

    # 验证用户消息
    user_message = page.locator(".message.user").first
    expect(user_message).to_be_visible()
    expect(user_message.locator(".message-content")).to_contain_text("测试消息")

    # 验证助手消息
    assistant_message = page.locator(".message.assistant").first
    expect(assistant_message).to_be_visible()


def test_suggestion_click(authenticated_page: Page):
    """测试点击建议问题。"""
    page = authenticated_page

    # 点击第一个建议按钮
    first_suggestion = page.locator(".suggestion-btn").first
    first_suggestion.click()

    # 验证输入框被填充
    question_input = page.locator("#questionInput")
    expect(question_input).not_to_have_value("")

    # 验证消息被发送
    page.wait_for_timeout(500)
    messages = page.locator(".message")
    expect(messages).to_have_count(2)


def test_streaming_response(authenticated_page: Page):
    """测试流式响应显示。"""
    page = authenticated_page

    # 发送消息
    question_input = page.locator("#questionInput")
    question_input.fill("测试流式响应")
    page.locator("#sendBtn").click()

    # 等待流式响应开始
    page.wait_for_timeout(200)

    # 验证助手消息存在
    assistant_message = page.locator(".message.assistant").first
    expect(assistant_message).to_be_visible()

    # 等待响应完成
    page.wait_for_timeout(2000)

    # 验证消息内容已更新
    message_content = assistant_message.locator(".message-content")
    expect(message_content).not_to_be_empty()


def test_empty_message_prevention(authenticated_page: Page):
    """测试防止发送空消息。"""
    page = authenticated_page

    # 尝试发送空消息
    send_btn = page.locator("#sendBtn")
    send_btn.click()

    # 验证没有消息被发送
    messages = page.locator(".message")
    expect(messages).to_have_count(0)


def test_enter_key_submit(authenticated_page: Page):
    """测试按 Enter 键提交消息。"""
    page = authenticated_page

    # 输入问题并按 Enter
    question_input = page.locator("#questionInput")
    question_input.fill("测试 Enter 键")
    question_input.press("Enter")

    # 验证消息被发送
    page.wait_for_timeout(500)
    messages = page.locator(".message")
    expect(messages).to_have_count(2)


def test_shift_enter_newline(authenticated_page: Page):
    """测试 Shift+Enter 插入换行。"""
    page = authenticated_page

    # 输入文本并按 Shift+Enter
    question_input = page.locator("#questionInput")
    question_input.fill("第一行")
    question_input.press("Shift+Enter")
    question_input.type("第二行")

    # 验证输入框包含换行
    value = question_input.input_value()
    assert "\n" in value


def test_message_timestamp(authenticated_page: Page):
    """测试消息时间戳显示。"""
    page = authenticated_page

    # 发送消息
    question_input = page.locator("#questionInput")
    question_input.fill("测试时间戳")
    page.locator("#sendBtn").click()

    # 等待消息渲染
    page.wait_for_timeout(500)

    # 验证时间戳存在
    timestamps = page.locator(".message-timestamp")
    expect(timestamps).to_have_count(2)  # 用户消息和助手消息都有时间戳


def test_message_avatars(authenticated_page: Page):
    """测试消息头像显示。"""
    page = authenticated_page

    # 发送消息
    question_input = page.locator("#questionInput")
    question_input.fill("测试头像")
    page.locator("#sendBtn").click()

    # 等待消息渲染
    page.wait_for_timeout(500)

    # 验证用户头像
    user_avatar = page.locator(".message.user .message-avatar")
    expect(user_avatar).to_be_visible()

    # 验证助手头像
    assistant_avatar = page.locator(".message.assistant .message-avatar")
    expect(assistant_avatar).to_be_visible()
