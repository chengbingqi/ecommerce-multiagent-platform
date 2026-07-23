export const GENERIC_LOGIN_ERROR = "用户名或密码错误，或登录暂时不可用。";

export interface LoginViewState {
  busy: boolean;
  message: string;
}

export type LoginViewEvent = { type: "start" } | { type: "success" } | { type: "failure" };

export function reduceLoginView(
  _state: LoginViewState,
  event: LoginViewEvent,
): LoginViewState {
  if (event.type === "start") return { busy: true, message: "" };
  if (event.type === "failure") return { busy: false, message: GENERIC_LOGIN_ERROR };
  return { busy: false, message: "" };
}
