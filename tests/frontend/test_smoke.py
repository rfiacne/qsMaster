"""
冒烟测试：验证页面基本加载和核心元素存在。
"""

from playwright.sync_api import Page, expect


def test_page_loads(authenticated_page: Page):
    """测试页面能够正常加载。"""
    page = authenticated_page

    # 验证页面标题
    expect(page).to_have_title("证券清算知识问答系统")

    # 验证主容器存在
    expect(page.locator(".app")).to_be_visible()

    # 验证侧边栏存在
    expect(page.locator(".sidebar")).to_be_visible()

    # 验证主内容区域存在
    expect(page.locator(".main")).to_be_visible()


def test_sidebar_navigation_visible(authenticated_page: Page):
    """测试侧边栏导航项可见。"""
    page = authenticated_page

    # 验证导航项存在
    nav_items = page.locator(".nav-item[data-page]")
    expect(nav_items).to_have_count(
        7
    )  # 问答、历史记录、导入文档、知识库检索、知识库状态、标准答案、审核队列

    # 验证第一个导航项（问答）默认激活
    first_nav = nav_items.first
    expect(first_nav).to_have_class(re.compile(r".*\bactive\b.*"))


def test_chat_page_default(authenticated_page: Page):
    """测试默认显示聊天页面。"""
    page = authenticated_page

    # 验证聊天页面激活
    chat_page = page.locator("#pageChat")
    expect(chat_page).to_have_class(re.compile(r".*\bactive\b.*"))

    # 验证空状态显示
    empty_state = page.locator(".empty-state")
    expect(empty_state).to_be_visible()

    # 验证输入框存在
    input_area = page.locator(".input-area")
    expect(input_area).to_be_visible()

    question_input = page.locator("#questionInput")
    expect(question_input).to_be_visible()

    # 验证发送按钮存在
    send_btn = page.locator("#sendBtn")
    expect(send_btn).to_be_visible()


def test_suggestion_buttons_visible(authenticated_page: Page):
    """测试建议问题按钮可见。"""
    page = authenticated_page

    # 验证建议按钮存在
    suggestion_btns = page.locator(".suggestion-btn")
    expect(suggestion_btns).to_have_count(4)

    # 验证第一个建议按钮文本
    first_btn = suggestion_btns.first
    expect(first_btn).to_contain_text("什么是")


def test_topbar_visible(authenticated_page: Page):
    """测试顶部栏可见。"""
    page = authenticated_page

    # 验证顶部栏存在
    topbar = page.locator(".topbar")
    expect(topbar).to_be_visible()

    # 验证标题显示
    title = page.locator(".topbar-title")
    expect(title).to_contain_text("问答")


def test_status_indicator(authenticated_page: Page):
    """测试状态指示器存在。"""
    page = authenticated_page

    # 验证状态点存在
    status_dot = page.locator(".status-dot")
    expect(status_dot).to_be_visible()

    # 验证状态文本存在
    status_text = page.locator("#statusText")
    expect(status_text).to_be_visible()
