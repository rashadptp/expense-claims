from django.urls import path

from . import manage_views, views

urlpatterns = [
    path("", views.home, name="home"),
    path("about/", views.about, name="about"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("claims/", views.claim_list, name="claim_list"),
    path("claims/new/", views.claim_create, name="claim_create"),
    path("claims/<int:pk>/review/", views.claim_review, name="claim_review"),
    path("claims/<int:pk>/", views.claim_detail, name="claim_detail"),
    path("claims/<int:pk>/pdf/", views.claim_pdf, name="claim_pdf"),
    path("claims/<int:pk>/decision/", views.claim_decision, name="claim_decision"),
    path("approvals/", views.approvals, name="approvals"),
    path("email-action/", views.email_action, name="email_action"),

    # Admin console
    path("manage/", manage_views.manage_home, name="manage_home"),
    path("manage/users/", manage_views.manage_users, name="manage_users"),
    path("manage/users/new/", manage_views.manage_user_create, name="manage_user_create"),
    path("manage/users/<int:pk>/", manage_views.manage_user_edit, name="manage_user_edit"),
    path("manage/branches/", manage_views.manage_branches, name="manage_branches"),
    path("manage/branches/new/", manage_views.manage_branch_create, name="manage_branch_create"),
    path("manage/branches/<int:pk>/", manage_views.manage_branch_edit, name="manage_branch_edit"),
]
