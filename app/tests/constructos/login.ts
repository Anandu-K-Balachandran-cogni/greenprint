// Credentials for the journeys: fixed local test values, or the deployment's login from the environment.
export const LOGIN = {
  id: process.env.CONSTRUCTOS_LOGIN_ID ?? 'tester',
  password: process.env.CONSTRUCTOS_LOGIN_PASSWORD ?? 'local-e2e-password',
};
export const AUTH_STATE = 'playwright/.auth/constructos.json';
