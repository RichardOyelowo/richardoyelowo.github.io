---
title: "Designing RBAC with Independent Org and Team Roles"
date: "2026-07-23"
summary: "Clustra's hierarchy: Org, Team, Project, Task. Membership layering, cascade deletes, and centralized permission checks."
pinned: true
---

# Designing RBAC with Independent Org and Team Roles

## What Clustra Is

Clustra is a multi-organization work management API with a vanilla JavaScript frontend. Think of it as a self-hosted alternative to the backend of Linear or Jira. The hierarchy is Org, then Team, then Project, then Task. Labels and milestones belong to projects. Activity is logged at the org level and covers every model type. Users can be members of multiple orgs, and within each org they can be members of multiple teams. Every layer has its own role-based access control.

The RBAC system is the foundation that everything else builds on. Getting the hierarchy, membership layering, and role separation right before adding more features was the most important architectural decision in this project. This post covers the design decisions that shaped the architecture, the edge cases I hit, and how I resolved them.

## The Hierarchy Decision

The Org, Team, Project, Task hierarchy was locked early. I considered flatter structures (just Org, Project, Task without teams) but the team layer adds real value: it lets an org separate work into functional groups (engineering, design, operations) while keeping shared project visibility across the org.

Labels and milestones belong to projects, not teams or orgs. This is a deliberate scoping decision. A label like "bug" means something specific within a project's context, and sharing labels across projects would create naming conflicts and permission complexity. If two projects both have a "critical" label, they should be independent.

Activity logging sits at the org level. Every create, update, and delete on any model (team, project, task, label, milestone) generates an activity entry tied to the org. This gives org admins a single audit trail without having to check per-project logs. The activity feed is the first thing you see when you open an org in Clustra, and it shows everything that happened across all of that org's teams and projects.

## Membership Layering: The Hard Way

My original approach was wrong. I allowed users to be added directly to teams without being org members first. This created a structural inconsistency: a user could be on a team inside an org they were not technically a member of. What happens when the team is deleted? What happens when the org is deleted? The user's membership becomes orphaned.

The correct architecture is layered. Org membership is a prerequisite for team membership. System users exist first. Then they become org members. Then, and only then, they can be added to teams within that org.

When the candidate endpoint for adding team members runs, it returns org members minus existing team members. Not all system users, not users from other orgs, just the valid subset. This means removing someone from an org correctly invalidates their team memberships through cascade deletes at the database level. You can never have a team member who is not also an org member. The membership graph is always consistent.

This was fixed mid-build. The migration that enforced the prerequisite constraint required updating the candidate queries, the add/remove endpoints, and the cascade delete configuration. It was a painful refactor but the alternative was leaving a data integrity bug in production.

## Independent Org and Team Roles

This is the design decision I am most confident about. Org roles and team roles are completely independent.

Org roles: Owner, Admin, Member
Team roles: Lead, Contributor, Viewer

A user can be an Org Admin but a Viewer on a specific team. Or an Org Member but a Team Lead. The roles answer different questions. The org role determines what the user can do at the organization level: create teams, manage org settings, invite members. The team role determines what the user can do within that specific team: create projects, manage tasks, update milestones.

Role checks happen at the service layer, not scattered across route handlers. I have centralized permission utility functions that take the user, the resource, and the required action, then return whether the action is allowed. This makes the permission logic auditable in one place instead of distributed across 30+ endpoints.

```python
# Centralized permission check
def check_org_permission(user, org, action):
    role = get_org_role(user.id, org.id)
    return PERMISSIONS.ORG[action].get(role, False)

def check_team_permission(user, team, action):
    org_role = get_org_role(user.id, team.org_id)
    team_role = get_team_role(user.id, team.id)
    # Admins bypass team membership checks on read-only routes
    if org_role in ('owner', 'admin') and action in READ_ACTIONS:
        return True
    return PERMISSIONS.TEAM[action].get(team_role, False)
```

Org admins bypass team membership checks on read-only routes. This means an admin can view any team's projects and tasks without being an explicit team member, which is useful for oversight. But write operations (creating tasks, updating milestones) still require team membership with an appropriate role. This distinction between read and write access for admins was a deliberate choice to give oversight capability without giving unchecked power.

## Automatic Team Lead Assignment

When a user creates a team, they are automatically added as a TeamMember with the Lead role. This prevents an edge case that would otherwise be possible: a team exists with no lead and no one who can manage it.

This is a simple hook in the service layer. After the team is created, insert a membership record with the creator's user ID and the Lead role. It is not a database trigger; it is application logic, which means it is testable and visible in the codebase. You can read the create team endpoint and see exactly what happens, including the lead assignment.

## Activity Logging: Atomic by Design

Every create, update, and delete in Clustra logs an activity entry. The design decision here was about when to commit.

The wrong approach: commit the activity log entry separately from the main operation. If the main operation succeeds but the log write fails, you have an operation with no audit trail. If the log write succeeds but the main operation fails, you have a phantom activity entry describing something that did not happen.

The correct approach: use `db.flush()` at the point of logging, which sends the INSERT to the database but does not commit, then commit everything together at the end of the service method. This keeps the activity log and the operation it describes in the same transaction. If either fails, both are rolled back.

```python
async def create_task(session, user, project, task_data):
    task = Task(project_id=project.id, created_by=user.id, **task_data)
    session.add(task)
    await session.flush()  # assigns task.id

    activity = Activity(
        org_id=project.org_id,
        user_id=user.id,
        action='create',
        model_type='task',
        model_id=task.id
    )
    session.add(activity)

    await session.commit()  # both task and activity committed together
```

This pattern is used consistently across every write operation in Clustra. The flush-then-commit approach means the activity entry gets the correct model_id (because flush assigns the auto-generated ID) while staying in the same transaction as the operation.

## Cascade Deletes: The Subtle Part

Cascade deletes were configured differently for different foreign key relationships, and each choice was deliberate.

The `created_by` and `assignee_id` fields on tasks use SET NULL. Deleting a user removes them as the creator or assignee but does not delete the task itself. The task still exists, it just no longer has a creator or assignee. This prevents data loss when a user leaves an org.

Membership and ownership relationships use CASCADE. Deleting a team removes all team memberships. Deleting an org removes all org memberships, which cascades to team memberships. This is correct because a membership record has no meaning without the entity it references.

Tasks within a project use CASCADE. Deleting a project removes all its tasks. Projects within a team use CASCADE. Deleting a team removes all its projects and their tasks. This is a full cleanup and it prevents orphaned records.

The SET NULL on user foreign keys was a deliberate choice that differentiates Clustra from systems that aggressively cascade everything. A task should survive user deletion. The alternative, cascading task deletion on user removal, would be destructive in a way that surprises users.

## Cross-Tenant Isolation

Every database query in Clustra is constructed with the authenticated user's ID as a hard filter at the ORM level. This is the same principle I used in Business Dashboard, but applied to a more complex hierarchy.

Listing tasks does not just filter by project. It filters by org membership, then by team membership, then by project:

```python
async def list_tasks(session, user, project_id):
    # Verify user has access to this project's org
    await verify_org_membership(session, user.id, project.org_id)
    # Verify user has access to this project's team
    await verify_team_membership(session, user.id, project.team_id)
    # Now query tasks
    return await session.scalars(
        select(Task).where(Task.project_id == project_id)
    )
```

Controller-level checks alone were considered insufficient. The filter exists at the query level regardless of what the route handler does. A developer adding a new endpoint cannot accidentally expose cross-tenant data because the query itself will not return it. The verification functions are called at the service layer before any data access happens.

## Current Status and Known Items

Clustra is complete and functional. The RBAC system, full CRUD across all models (orgs, teams, projects, tasks, labels, milestones), activity logging, and the vanilla frontend are all built and working. There are two known items from the v1 build that are recognized but not yet addressed:

**Label color field.** The color picker exists in the frontend form and CSS. The Label model does not have a color column yet. A migration is needed to add a `color: Mapped[str]` column with a default value. The frontend already sends the color, but the backend ignores it.

**Org member candidates.** The team add member modal uses a proper dropdown with candidates. The org add member form still takes a user_id manually. An org-level candidates endpoint that returns all system users who are not already org members was considered but not built. This would make the org member addition flow consistent with how team member addition works.

These are recognized gaps, not blockers. The system is functional and deployed.

---

*Code at [github.com/RichardOyelowo/clustra](https://github.com/RichardOyelowo/clustra)*
