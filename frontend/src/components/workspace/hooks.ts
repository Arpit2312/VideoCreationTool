import { useQuery } from "@tanstack/react-query";
import { api, ApiError } from "../../api";

/** Load a workspace JSON file; resolves to null when it does not exist yet. */
export function useWorkspaceJson<T>(videoId: string, path: string, enabled = true) {
  return useQuery({
    queryKey: ["vfile", videoId, path],
    enabled,
    retry: false,
    queryFn: async (): Promise<T | null> => {
      try {
        return await api.fileJson<T>(videoId, path);
      } catch (e) {
        if (e instanceof ApiError && (e.status === 404 || e.status === 400)) return null;
        if (e instanceof SyntaxError) throw new Error(`${path} is not valid JSON: ${e.message}`);
        throw e;
      }
    },
  });
}

export function useWorkspaceFiles(videoId: string, path: string, enabled = true) {
  return useQuery({
    queryKey: ["files", videoId, path],
    enabled,
    retry: false,
    queryFn: async () => {
      try {
        return await api.files(videoId, path);
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) return [];
        throw e;
      }
    },
  });
}
