import { X } from "lucide-react";
import { useEffect, useId, useRef, type ReactNode } from "react";

import { Button } from "./Button";

type Props = { title: string; onClose: () => void; children: ReactNode };

/** Mount it to open it. The native dialog handles focus, Escape and the backdrop layer. */
export function Dialog({ title, onClose, children }: Props) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();

  useEffect(() => {
    const dialog = ref.current;
    if (dialog && !dialog.open) dialog.showModal?.();
    return () => dialog?.close?.();
  }, []);

  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onMouseDown={(event) => {
        if (event.target === ref.current) onClose();
      }}
      className="sf-dialog bg-raised border-line-strong text-bone shadow-float m-auto w-[calc(100%-2rem)] max-w-[440px] rounded-xl border p-0"
    >
      <div className="p-5">
        <div className="mb-4 flex items-start justify-between gap-3">
          <h2 id={titleId} className="marquee text-[30px] leading-8">
            {title}
          </h2>
          <Button variant="ghost" size="icon" aria-label="Cerrar" onClick={onClose}>
            <X aria-hidden className="size-4" />
          </Button>
        </div>
        {children}
      </div>
    </dialog>
  );
}
