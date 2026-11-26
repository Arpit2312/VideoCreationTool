import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import { FolderPlus, Trash2 } from "lucide-react";
import { api, explainError } from "../api";
import { fmtDate } from "../lib/format";
import { VideoRow } from "../components/VideoRow";
import { cx, Empty, ErrorBox, Loading, PageHeader, Section } from "../components/ui";

export default function Projects() {
  const { id } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const projects = useQuery({ queryKey: ["projects"], queryFn: api.projects });
  const selected = id || projects.data?.[0]?.id;
  const videos = useQuery({
    queryKey: ["videos", { project_id: selected }],
    queryFn: () => api.videos({ project_id: selected }),
    enabled: !!selected,
  });
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const create = useMutation({
    mutationFn: () => api.createProject({ name, description: desc }),
    onSuccess: (p) => {
      setName("");
      setDesc("");
      qc.invalidateQueries({ queryKey: ["projects"] });
      nav(`/projects/${p.id}`);
    },
  });
  const remove = useMutation({
    mutationFn: (pid: string) => api.deleteProject(pid),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["projects"] });
      nav("/projects");
    },
  });
  const current = projects.data?.find((p) => p.id === selected);

  return (
    <div>
      <PageHeader title="Projects" subtitle="Group videos into projects. Open a video to see its production timeline." />
      {projects.error && <ErrorBox error={projects.error} onRetry={() => projects.refetch()} />}
      <div className="grid lg:grid-cols-[280px_1fr] gap-4">
        <div className="space-y-4">
          <Section title="Projects">
            {projects.isLoading ? (
              <Loading />
            ) : (
              <ul className="space-y-1">
                {(projects.data || []).map((p) => (
                  <li key={p.id}>
                    <Link
                      to={`/projects/${p.id}`}
                      className={cx("block rounded-lg px-3 py-2 text-sm", p.id === selected ? "bg-accent/15 font-medium" : "hover:bg-panel2")}
                    >
                      {p.name}
                      <div className="subtle">{fmtDate(p.created_at)}</div>
                    </Link>
                  </li>
                ))}
                {!projects.data?.length && <li className="subtle">No projects yet.</li>}
              </ul>
            )}
          </Section>
          <Section title="New project">
            <form
              className="space-y-2"
              onSubmit={(e) => {
                e.preventDefault();
                if (name.trim()) create.mutate();
              }}
            >
              <input className="input" placeholder="Project name" value={name} onChange={(e) => setName(e.target.value)} />
              <textarea className="input" rows={2} placeholder="Description (optional)" value={desc} onChange={(e) => setDesc(e.target.value)} />
              <button className="btn-primary w-full" disabled={!name.trim() || create.isPending}>
                <FolderPlus className="h-4 w-4" /> Create project
              </button>
              {create.error && <p className="text-xs text-red-600 dark:text-red-300">{explainError(create.error)}</p>}
            </form>
          </Section>
        </div>
        <div>
          {current ? (
            <Section
              title={current.name}
              actions={
                <div className="flex gap-2">
                  <Link to={`/new?project=${current.id}`} className="btn btn-sm">
                    New video here
                  </Link>
                  <button
                    className="btn-danger btn-sm"
                    onClick={() => window.confirm(`Delete project "${current.name}"?`) && remove.mutate(current.id)}
                  >
                    <Trash2 className="h-3.5 w-3.5" /> Delete
                  </button>
                </div>
              }
            >
              {current.description && <p className="text-sm text-muted mb-3">{current.description}</p>}
              {remove.error && <ErrorBox error={remove.error} />}
              {videos.isLoading ? (
                <Loading />
              ) : videos.data?.length ? (
                <div className="space-y-2">
                  {videos.data.map((v) => (
                    <VideoRow key={v.id} video={v} />
                  ))}
                </div>
              ) : (
                <Empty>No videos in this project yet.</Empty>
              )}
            </Section>
          ) : (
            !projects.isLoading && <Empty>Create a project to get started.</Empty>
          )}
        </div>
      </div>
    </div>
  );
}
