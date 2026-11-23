import { useId, type FormEvent } from "react";
import { toast } from "sonner";

import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { Field, inputClass } from "../../components/ui/Field";
import { Notice } from "../../components/ui/Notice";
import { errorMessage, fieldErrors } from "../../lib/errors";
import { useDeleteVideo, useUpdateVideo, type Video } from "./api";

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
