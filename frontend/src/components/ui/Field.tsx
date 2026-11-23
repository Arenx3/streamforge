import { useId, type InputHTMLAttributes, type ReactNode } from "react";

type Props = InputHTMLAttributes<HTMLInputElement> & {
  label: string;
  error?: string;
  hint?: ReactNode;
  aside?: ReactNode;
};

export const inputClass =
  "bg-raised border-line-strong text-bone placeholder:text-muted hover:border-muted focus-visible:border-brass h-10 w-full rounded-[7px] border px-3 text-sm transition-[border-color] outline-none focus-visible:outline-2 focus-visible:outline-offset-0 aria-invalid:border-danger";

export function Field({ label, error, hint, aside, className = "", ...input }: Props) {
  const id = useId();
  const noteId = `${id}-note`;
  const note = error ?? hint;
  return (
    <div className={className}>
      <div className="mb-1.5 flex items-baseline justify-between gap-3">
        <label htmlFor={id} className="text-bone-soft text-[13px] font-medium">
          {label}
        </label>
        {aside}
      </div>
      <input
        id={id}
        aria-invalid={error ? true : undefined}
        aria-describedby={note ? noteId : undefined}
        className={inputClass}
        {...input}
      />
      {note && (
        <p id={noteId} className={`mt-1.5 text-[13px] ${error ? "text-danger" : "text-muted"}`}>
          {note}
        </p>
      )}
    </div>
  );
}
