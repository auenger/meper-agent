/**
 * Route definitions — AppLayout wraps all pages with sidebar + header.
 *
 * ProtectedRoute guards authenticated routes; login & design-system are public.
 */
import AppLayout from '../components/AppLayout'
import { ProtectedRoute } from './protected-routes'
import { PermissionRoute } from './permission-route'
import DashboardPage from '../pages/dashboard-page'
import AgentsPage from '../pages/agents-page'
import AgentDetailPage from '../pages/agent-detail-page'
import ModelsPage from '../pages/models-page'
import SkillsPage from '../pages/skills-page'
import McpPage from '../pages/mcp-page'
import ExternalAuthPage from '../pages/external-auth-page'
import TasksPage from '../pages/tasks-page'
import WorkflowsPage from '../pages/workflows-page'
import WorkflowDetailPage from '../pages/workflow-detail-page'
import ToolsPage from '../pages/tools-page'
import KnowledgePage from '../pages/knowledge-page'
import KnowledgeDetailPage from '../pages/knowledge-detail-page'
import SkillDetailPage from '../pages/skill-detail-page'
import ExecutionStatsPage from '../pages/execution-stats-page'
import ApiKeysPage from '../pages/api-keys-page'
import CredentialsPage from '../pages/credentials-page'
import ChannelsPage from '../pages/channels-page'
import UsersPage from '../pages/users-page'
import RolesPage from '../pages/roles-page'
import SettingsPage from '../pages/settings-page'
import UserSkillDetailPage from '../pages/user-skill-detail-page'
import { DesignSystemPage } from '../pages/design-system-page'
import DesignReferencePage from '../pages/design-reference-page'
import { LoginPage } from '../pages/login-page'

export const routes = [
  {
    element: <ProtectedRoute />,
    children: [
      {
        element: <AppLayout />,
        children: [
          { path: '/', element: <DashboardPage /> },
          { path: '/dashboard', element: <DashboardPage /> },
          { path: '/agents', element: <PermissionRoute perm="agent:read"><AgentsPage /></PermissionRoute> },
          { path: '/agents/:id', element: <PermissionRoute perm="agent:read"><AgentDetailPage /></PermissionRoute> },
          { path: '/models', element: <PermissionRoute perm="model:read"><ModelsPage /></PermissionRoute> },
          { path: '/skills', element: <SkillsPage /> },
          { path: '/mcp', element: <PermissionRoute perm="mcp:read"><McpPage /></PermissionRoute> },
          { path: '/external-auth', element: <ExternalAuthPage /> },
          { path: '/tasks', element: <PermissionRoute perm="task:read"><TasksPage /></PermissionRoute> },
          { path: '/workflows', element: <PermissionRoute perm="workflow:read"><WorkflowsPage /></PermissionRoute> },
          { path: '/workflows/:id', element: <PermissionRoute perm="workflow:read"><WorkflowDetailPage /></PermissionRoute> },
          { path: '/tools', element: <PermissionRoute perm="tool:read"><ToolsPage /></PermissionRoute> },
          { path: '/knowledge', element: <PermissionRoute perm="knowledge:read"><KnowledgePage /></PermissionRoute> },
          { path: '/knowledge/:id', element: <PermissionRoute perm="knowledge:read"><KnowledgeDetailPage /></PermissionRoute> },
          { path: '/skills/:id', element: <SkillDetailPage /> },
          { path: '/my-skills/:id', element: <UserSkillDetailPage /> },
          { path: '/execution-stats', element: <PermissionRoute perm="apikey:manage"><ExecutionStatsPage /></PermissionRoute> },
          { path: '/api-keys', element: <PermissionRoute perm="apikey:manage"><ApiKeysPage /></PermissionRoute> },
          { path: '/credentials', element: <PermissionRoute perm="tool:read"><CredentialsPage /></PermissionRoute> },
          { path: '/channels', element: <PermissionRoute perm="tool:read"><ChannelsPage /></PermissionRoute> },
          { path: '/users', element: <PermissionRoute perm="user:read"><UsersPage /></PermissionRoute> },
          { path: '/roles', element: <PermissionRoute perm="user:read"><RolesPage /></PermissionRoute> },
          { path: '/settings', element: <PermissionRoute perm="settings:manage"><SettingsPage /></PermissionRoute> },
        ],
      },
    ],
  },
  { path: '/design-system', element: <DesignSystemPage /> },
  { path: '/design-reference', element: <DesignReferencePage /> },
  { path: '/login', element: <LoginPage /> },
  { path: '*', element: <DashboardPage /> },
]
