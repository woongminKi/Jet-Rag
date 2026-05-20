'use client';

import { createContext, useContext } from 'react';

/**
 * D1 Phase B — UI auth 상태 컨텍스트 (plan §1.1).
 *
 * 서버 컴포넌트(layout)가 검증된 초기 user 를 주입한다. browser client getSession()
 * 에 의존하지 않는다(httpOnly 라 JS 가 못 읽음). 로그아웃은 server action(signOut)으로
 * 처리하므로 컨텍스트는 표시용 상태만 보유한다.
 */

export interface AuthUser {
  id: string;
  email: string | null;
}

interface AuthContextValue {
  user: AuthUser | null;
}

const AuthContext = createContext<AuthContextValue>({ user: null });

export function AuthProvider({
  user,
  children,
}: {
  user: AuthUser | null;
  children: React.ReactNode;
}) {
  return (
    <AuthContext.Provider value={{ user }}>{children}</AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  return useContext(AuthContext);
}
