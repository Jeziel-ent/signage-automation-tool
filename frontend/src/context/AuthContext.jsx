import { createContext, useContext } from "react";

/**
 * Provided by components/AuthGate.jsx:
 *   user         {id, email, name, role} | null
 *   authEnabled  false = the server runs with SIGNAGE_AUTH=off (everybody is an administrator, no sign-in anywhere)
 *   isAdmin      administrator (or login off)
 *   needsSetup   no account exists yet: the sign-in card creates the first administrator
 *   login(email, password, name?)  -> Promise<string | null>: null when signed in, else the message to show
 *   logout()
 */
export const AuthContext = createContext({
  user: null, authEnabled: false, isAdmin: true, needsSetup: false, login: async () => null, logout: async () => {},
});

export function useAuth() {
  return useContext(AuthContext);
}
