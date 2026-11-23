import { Check, Copy, ExternalLink } from "lucide-react";
import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { toast } from "sonner";

import { Button, buttonClass } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { Field, inputClass } from "../../components/ui/Field";
import { Notice } from "../../components/ui/Notice";
import { errorMessage, fieldErrors } from "../../lib/errors";
import {
  shareUrl,
  useDeleteVideo,
  useUpdateVideo,
  useVideoSharing,
  type Video,
  type VideoDetail,
} from "./api";

type Props = { video: Video; onClose: () => void; onDeleted?: () => void };

export function EditVideoDialog({ video, onClose }: Props) {
  const update = useUpdateVideo();
  const descriptionId = useId();
  const errors = fieldErrors(update.error);
  const general = update.isError && !errors.title && !errors.description;

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    update.mutate(
      {
        id: video.id,
        title: String(form.get("title")),
        description: String(form.get("description")),
      },
      {
        onSuccess: () => {
          toast.success("Cambios guardados");
          onClose();
        },
      },
    );
  };

  return (
    <Dialog title="Editar video" onClose={onClose}>
      {general && (
        <Notice tone="error" className="mb-4">
          {errorMessage(update.error)}
        </Notice>
      )}
      <form className="space-y-4" onSubmit={submit}>
        <Field
          label="Título"
          name="title"
          defaultValue={video.title}
          required
          maxLength={255}
          error={errors.title}
        />
        <div>
          <label htmlFor={descriptionId} className="text-bone-soft mb-1.5 block text-[13px] font-medium">
            Descripción
          </label>
          <textarea
            id={descriptionId}
            name="description"
            defaultValue={video.description}
            maxLength={5000}
            rows={4}
            className={`${inputClass} h-auto resize-y py-2 leading-5`}
          />
          {errors.description && <p className="text-danger mt-1.5 text-[13px]">{errors.description}</p>}
        </div>
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancelar
          </Button>
          <Button type="submit" loading={update.isPending}>
            Guardar
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

const COPIED_MS = 1600;

async function copyText(text: string, field: HTMLInputElement | null) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // Outside HTTPS and localhost the browser has no clipboard API.
    field?.select();
    return document.execCommand("copy");
  }
}

export function ShareVideoDialog({ video, onClose }: { video: VideoDetail; onClose: () => void }) {
  const { create, revoke } = useVideoSharing(video.id);
  const field = useRef<HTMLInputElement>(null);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const [copied, setCopied] = useState(false);
  useEffect(() => () => clearTimeout(timer.current), []);

  const url = video.share_token ? shareUrl(video.share_token) : null;
  const failure = create.error ?? revoke.error;
  const name = <strong className="text-bone font-semibold break-words">{video.title}</strong>;

  const copy = async () => {
    if (!url) return;
    if (!(await copyText(url, field.current))) {
      toast.error("No se pudo copiar. Selecciona el enlace y cópialo.");
      return;
    }
    setCopied(true);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setCopied(false), COPIED_MS);
  };

  return (
    <Dialog title="Compartir video" onClose={onClose}>
      {failure && (
        <Notice tone="error" className="mb-4">
          {errorMessage(failure)}
        </Notice>
      )}
      {url ? (
        <>
          <p className="text-bone-soft">Cualquiera con este enlace puede ver {name} sin crear una cuenta.</p>
          <div className="mt-4 flex gap-2">
            <input
              ref={field}
              readOnly
              aria-label="Enlace público"
              value={url}
              onFocus={(event) => event.currentTarget.select()}
              className={`${inputClass} tabular min-w-0 flex-1`}
            />
            <Button className="min-w-[108px]" onClick={() => void copy()}>
              {copied ? <Check aria-hidden className="size-4" /> : <Copy aria-hidden className="size-4" />}
              {copied ? "Copiado" : "Copiar"}
            </Button>
          </div>
          <p className="text-muted mt-2 text-[13px]">
            Si lo desactivas deja de abrir al instante. Quien ya lo esté viendo puede seguir hasta una hora.
          </p>
          <div className="mt-5 flex flex-wrap justify-between gap-2">
            <Button
              variant="ghost"
              loading={revoke.isPending}
              onClick={() =>
                revoke.mutate(undefined, { onSuccess: () => toast.success("Enlace desactivado") })
              }
            >
              Desactivar enlace
            </Button>
            <a href={url} target="_blank" rel="noopener" className={buttonClass("secondary")}>
              Abrir enlace
              <ExternalLink aria-hidden className="size-3.5" />
            </a>
          </div>
        </>
      ) : (
        <>
          <p className="text-bone-soft">
            Crea un enlace público para {name}. Quien lo tenga podrá verlo sin crear una cuenta.
          </p>
          <div className="mt-5 flex justify-end gap-2">
            <Button variant="ghost" onClick={onClose}>
              Cancelar
            </Button>
            <Button loading={create.isPending} onClick={() => create.mutate()}>
              Crear enlace
            </Button>
          </div>
        </>
      )}
    </Dialog>
  );
}

export function DeleteVideoDialog({ video, onClose, onDeleted }: Props) {
  const remove = useDeleteVideo();

  const confirm = () =>
    remove.mutate(video.id, {
      onSuccess: () => {
        toast.success("Video borrado");
        onClose();
        onDeleted?.();
      },
    });

  return (
    <Dialog title="Borrar video" onClose={onClose}>
      {remove.isError && (
        <Notice tone="error" className="mb-4">
          {errorMessage(remove.error)}
        </Notice>
      )}
      <p className="text-bone-soft">
        Se borrarán <strong className="text-bone font-semibold break-words">{video.title}</strong> y
        todos sus archivos. Esta acción no se puede deshacer.
      </p>
      <div className="mt-5 flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>
          Cancelar
        </Button>
        <Button variant="danger" loading={remove.isPending} onClick={confirm}>
          Borrar video
        </Button>
      </div>
    </Dialog>
  );
}
