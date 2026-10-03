"""Every page of the main window. Each page has a title, a subtitle and on_show() (called whenever it is opened)."""


def build_pages(session, shell):
    from .connections import ConnectionsPage
    from .crm import CrmPage
    from .dashboard import DashboardPage
    from .logs import LogsPage
    from .nbo import NboPage
    from .results import ResultsPage
    from .review import ReviewPage
    from .search import SearchPage
    from .settings_page import SettingsPage
    from .workflow_page import WorkflowPage
    from .insights_pages import AccuracyPage, ControlRoomPage
    from .triage import TriagePage
    from .users import UsersPage
    from .execution_page import ExecutionPage
    from .backlog_page import BacklogPage
    return {
        "dashboard": DashboardPage(session, shell),
        "backlog": BacklogPage(session, shell),
        "review": ReviewPage(session, shell),
        "results": ResultsPage(session, shell),
        "workflow": WorkflowPage(session, shell),
        "triage": TriagePage(session, shell),
        "control": ControlRoomPage(session, shell),
        "accuracy": AccuracyPage(session, shell),
        "users": UsersPage(session, shell),
        "execution": ExecutionPage(session, shell),
        "search": SearchPage(session, shell),
        "nbo": NboPage(session, shell),
        "crm": CrmPage(session, shell),
        "connections": ConnectionsPage(session, shell),
        "logs": LogsPage(session, shell),
        "settings": SettingsPage(session, shell),
    }
