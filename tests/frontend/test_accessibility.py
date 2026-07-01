"""
可访问性测试：验证键盘导航、ARIA 标签、焦点管理等。
"""

from playwright.sync_api import Page, expect


def test_keyboard_navigation_sidebar(authenticated_page: Page):
    """测试侧边栏键盘导航。"""
    page = authenticated_page

    # 按 Tab 键导航到侧边栏
    for _ in range(10):  # 多次 Tab 确保到达侧边栏
        page.keyboard.press("Tab")

    # 验证焦点在某个可聚焦元素上
    focused = page.locator(":focus")
    expect(focused).to_be_visible()


def test_keyboard_navigation_nav_items(authenticated_page: Page):
    """测试导航项键盘触发。"""
    page = authenticated_page

    # 聚焦到第一个导航项
    first_nav = page.locator(".nav-item[data-page]").first
    first_nav.focus()

    # 按 Enter 键触发
    first_nav.press("Enter")

    # 验证页面切换
    chat_page = page.locator("#pageChat")
    expect(chat_page).to_have_class(re.compile(r".*\bactive\b.*"))


def test_aria_labels_sidebar(authenticated_page: Page):
    """测试侧边栏 ARIA 标签。"""
    page = authenticated_page

    # 验证侧边栏有 role="complementary"
    sidebar = page.locator(".sidebar")
    expect(sidebar).to_have_attribute("role", "complementary")

    # 验证导航有 role="navigation"
    nav = page.locator(".sidebar-nav")
    expect(nav).to_have_attribute("role", "navigation")


def test_aria_labels_chat_area(authenticated_page: Page):
    """测试聊天区域 ARIA 标签。"""
    page = authenticated_page

    # 验证聊天区域有 role="log"
    chat_area = page.locator("#chatArea")
    expect(chat_area).to_have_attribute("role", "log")

    # 验证有 aria-live="polite"
    expect(chat_area).to_have_attribute("aria-live", "polite")


def test_aria_labels_input(authenticated_page: Page):
    """测试输入框 ARIA 标签。"""
    page = authenticated_page

    # 验证输入框有 aria-label
    question_input = page.locator("#questionInput")
    expect(question_input).to_have_attribute("aria-label", "输入问题")

    # 验证发送按钮有 aria-label
    send_btn = page.locator("#sendBtn")
    expect(send_btn).to_have_attribute("aria-label", "发送问题")


def test_aria_labels_modal(authenticated_page: Page):
    """测试模态框 ARIA 标签。"""
    page = authenticated_page

    # 打开关于模态框
    about_nav = page.locator(".nav-item").filter(has_text="关于")
    about_nav.click()

    # 验证模态框有 role="dialog"
    modal = page.locator("#aboutModal")
    expect(modal).to_have_attribute("role", "dialog")

    # 验证有 aria-modal="true"
    expect(modal).to_have_attribute("aria-modal", "true")

    # 验证有 aria-labelledby
    expect(modal).to_have_attribute("aria-labelledby", "aboutModalTitle")


def test_focus_visible_styles(authenticated_page: Page):
    """测试焦点可见样式。"""
    page = authenticated_page

    # 聚焦到发送按钮
    send_btn = page.locator("#sendBtn")
    send_btn.focus()

    # 验证按钮获得焦点
    expect(send_btn).to_be_focused()


def test_aria_hidden_decorative(authenticated_page: Page):
    """测试装饰性元素的 aria-hidden。"""
    page = authenticated_page

    # 验证装饰性图标有 aria-hidden="true"
    decorative_icons = page.locator(".nav-item .icon")
    for icon in decorative_icons.all():
        expect(icon).to_have_attribute("aria-hidden", "true")


def test_aria_current_page(authenticated_page: Page):
    """测试当前页面的 aria-current 属性。"""
    page = authenticated_page

    # 验证默认页面（问答）有 aria-current="page"
    active_nav = page.locator(".nav-item.active")
    expect(active_nav).to_have_attribute("aria-current", "page")


def test_status_role(authenticated_page: Page):
    """测试状态元素的 role 属性。"""
    page = authenticated_page

    # 验证状态文本有 role="status"
    status_text = page.locator("#statusText")
    expect(status_text).to_have_attribute("role", "status")


def test_search_results_aria_live(authenticated_page: Page):
    """测试搜索结果区域的 aria-live。"""
    page = authenticated_page

    # 导航到知识库检索页面
    page.locator('[data-page="search-kb"]').click()

    # 验证搜索结果区域有 aria-live="polite"
    search_results = page.locator("#searchResults")
    expect(search_results).to_have_attribute("aria-live", "polite")


def test_upload_zone_keyboard(authenticated_page: Page):
    """测试上传区域键盘操作。"""
    page = authenticated_page

    # 导航到导入页面
    page.locator('[data-page="import"]').click()

    # 聚焦到上传区域
    upload_zone = page.locator("#uploadZone")
    upload_zone.focus()

    # 验证上传区域获得焦点
    expect(upload_zone).to_be_focused()

    # 验证有 role="button"
    expect(upload_zone).to_have_attribute("role", "button")


def test_toolbar_role(authenticated_page: Page):
    """测试工具栏的 role 属性。"""
    page = authenticated_page

    # 验证聊天工具栏有 role="toolbar"
    toolbar = page.locator(".chat-toolbar")
    expect(toolbar).to_have_attribute("role", "toolbar")


def test_progress_bar_aria(authenticated_page: Page):
    """测试进度条 ARIA 属性。"""
    page = authenticated_page

    # 导航到导入页面
    page.locator('[data-page="import"]').click()

    # 验证进度条有 role="progressbar"
    progress = page.locator("#uploadProgress")
    expect(progress).to_have_attribute("role", "progressbar")

    # 验证有 aria-valuemin
    expect(progress).to_have_attribute("aria-valuemin", "0")

    # 验证有 aria-valuemax
    expect(progress).to_have_attribute("aria-valuemax", "100")
