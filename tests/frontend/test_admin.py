"""
管理页面测试：验证导入文档、知识库检索、标准答案、审核队列等管理功能。
"""

from playwright.sync_api import Page, expect


def test_import_page_navigation(authenticated_page: Page):
    """测试导航到导入文档页面。"""
    page = authenticated_page

    # 点击导入文档导航项
    import_nav = page.locator('[data-page="import"]')
    import_nav.click()

    # 验证页面切换
    import_page = page.locator("#pageImport")
    expect(import_page).to_have_class(re.compile(r".*\bactive\b.*"))

    # 验证上传区域存在
    upload_zone = page.locator(".upload-zone")
    expect(upload_zone).to_be_visible()

    # 验证文件输入存在
    file_input = page.locator("#fileInput")
    expect(file_input).to_be_attached()


def test_search_kb_page_navigation(authenticated_page: Page):
    """测试导航到知识库检索页面。"""
    page = authenticated_page

    # 点击知识库检索导航项
    search_nav = page.locator('[data-page="search-kb"]')
    search_nav.click()

    # 验证页面切换
    search_page = page.locator("#pageSearchKB")
    expect(search_page).to_have_class(re.compile(r".*\bactive\b.*"))

    # 验证搜索输入框存在
    search_input = page.locator("#searchInput")
    expect(search_input).to_be_visible()

    # 验证搜索按钮存在
    search_btn = page.locator("#searchBtn")
    expect(search_btn).to_be_visible()


def test_kb_status_page_navigation(authenticated_page: Page):
    """测试导航到知识库状态页面。"""
    page = authenticated_page

    # 点击知识库状态导航项
    status_nav = page.locator('[data-page="kb-status"]')
    status_nav.click()

    # 验证页面切换
    status_page = page.locator("#pageKbStatus")
    expect(status_page).to_have_class(re.compile(r".*\bactive\b.*"))

    # 验证统计卡片存在
    stat_cards = page.locator(".stat-card")
    expect(stat_cards).to_have_count(4)  # 文档数量、文档片段、索引大小、最近更新


def test_standards_page_navigation(authenticated_page: Page):
    """测试导航到标准答案页面。"""
    page = authenticated_page

    # 点击标准答案导航项
    standards_nav = page.locator('[data-page="standards"]')
    standards_nav.click()

    # 验证页面切换
    standards_page = page.locator("#pageStandards")
    expect(standards_page).to_have_class(re.compile(r".*\bactive\b.*"))

    # 验证搜索输入框存在
    std_search = page.locator("#stdSearchInput")
    expect(std_search).to_be_visible()

    # 验证状态筛选器存在
    std_filter = page.locator("#stdStatusFilter")
    expect(std_filter).to_be_visible()


def test_reviews_page_navigation(authenticated_page: Page):
    """测试导航到审核队列页面。"""
    page = authenticated_page

    # 点击审核队列导航项
    reviews_nav = page.locator('[data-page="reviews"]')
    reviews_nav.click()

    # 验证页面切换
    reviews_page = page.locator("#pageReviews")
    expect(reviews_page).to_have_class(re.compile(r".*\bactive\b.*"))

    # 验证状态筛选器存在
    rev_filter = page.locator("#revStatusFilter")
    expect(rev_filter).to_be_visible()


def test_history_page_navigation(authenticated_page: Page):
    """测试导航到历史记录页面。"""
    page = authenticated_page

    # 点击历史记录导航项
    history_nav = page.locator('[data-page="history"]')
    history_nav.click()

    # 验证页面切换
    history_page = page.locator("#pageHistory")
    expect(history_page).to_have_class(re.compile(r".*\bactive\b.*"))

    # 验证服务端会话区域存在
    server_sessions = page.locator("#serverSessionList")
    expect(server_sessions).to_be_visible()

    # 验证本地历史区域存在
    local_history = page.locator("#historyList")
    expect(local_history).to_be_visible()


def test_import_page_metadata_form(authenticated_page: Page):
    """测试导入页面的元数据表单。"""
    page = authenticated_page

    # 导航到导入页面
    page.locator('[data-page="import"]').click()

    # 验证来源机构选择器
    source_select = page.locator("#metaSource")
    expect(source_select).to_be_visible()

    # 验证文档类别选择器
    category_select = page.locator("#metaCategory")
    expect(category_select).to_be_visible()

    # 验证生效日期输入
    date_input = page.locator("#metaDate")
    expect(date_input).to_be_visible()

    # 验证版本号输入
    version_input = page.locator("#metaVersion")
    expect(version_input).to_be_visible()

    # 验证标签输入
    tags_input = page.locator("#metaTags")
    expect(tags_input).to_be_visible()

    # 验证备注描述输入
    desc_input = page.locator("#metaDesc")
    expect(desc_input).to_be_visible()


def test_import_page_buttons(authenticated_page: Page):
    """测试导入页面的按钮。"""
    page = authenticated_page

    # 导航到导入页面
    page.locator('[data-page="import"]').click()

    # 验证开始导入按钮存在且禁用
    upload_btn = page.locator("#uploadBtn")
    expect(upload_btn).to_be_visible()
    expect(upload_btn).to_be_disabled()

    # 验证清空文件列表按钮存在
    clear_btn = page.locator("#clearFilesBtn")
    expect(clear_btn).to_be_visible()


def test_search_kb_page_search_functionality(authenticated_page: Page):
    """测试知识库检索页面的搜索功能。"""
    page = authenticated_page

    # 导航到知识库检索页面
    page.locator('[data-page="search-kb"]').click()

    # 输入搜索关键词
    search_input = page.locator("#searchInput")
    search_input.fill("测试搜索")

    # 点击搜索按钮
    search_btn = page.locator("#searchBtn")
    search_btn.click()

    # 验证搜索结果区域更新
    search_results = page.locator("#searchResults")
    expect(search_results).to_be_visible()


def test_kb_status_page_refresh(authenticated_page: Page):
    """测试知识库状态页面的刷新功能。"""
    page = authenticated_page

    # 导航到知识库状态页面
    page.locator('[data-page="kb-status"]').click()

    # 验证刷新按钮存在
    refresh_btn = page.locator("#pageKbStatus .btn-primary")
    expect(refresh_btn).to_be_visible()
    expect(refresh_btn).to_contain_text("刷新")


def test_standards_page_add_form(authenticated_page: Page):
    """测试标准答案页面的新增表单。"""
    page = authenticated_page

    # 导航到标准答案页面
    page.locator('[data-page="standards"]').click()

    # 点击新增按钮
    add_btn = page.locator("#pageStandards .btn-secondary")
    add_btn.click()

    # 验证新增表单显示
    add_form = page.locator("#addStandardForm")
    expect(add_form).to_be_visible()

    # 验证表单字段存在
    question_input = page.locator("#newStdQ")
    expect(question_input).to_be_visible()

    answer_input = page.locator("#newStdA")
    expect(answer_input).to_be_visible()

    category_input = page.locator("#newStdCat")
    expect(category_input).to_be_visible()
