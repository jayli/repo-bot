import { readFileSync } from "fs";

export class UserService {
  getUser(id: string) {
    return loadUser(id);
  }
}

export function loadUser(id: string) {
  return readFileSync(id, "utf8");
}

export function handler() {
  const service = new UserService();
  return service.getUser("42");
}
