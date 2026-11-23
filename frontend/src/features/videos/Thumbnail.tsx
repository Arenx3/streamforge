import { Film } from "lucide-react";
import { useState } from "react";

type Props = { url: string | null; className?: string };

/** Each API response signs a new URL, so the first one is kept to avoid re-downloading. */
export function Thumbnail({ url, className = "" }: Props) {
  const [source, setSource] = useState(url);
  const [failed, setFailed] = useState(false);
  if (!source && url) setSource(url);

  const onError = () => {
    if (url && url !== source) setSource(url);
    else setFailed(true);
  };

  return (
    <span className={`bg-overlay relative block shrink-0 overflow-hidden rounded ${className}`}>
      {source && !failed ? (
        <img
          src={source}
          alt=""
          loading="lazy"
          decoding="async"
          onError={onError}
          className="size-full object-cover"
        />
      ) : (
        <span className="text-line-strong grid size-full place-items-center">
          <Film aria-hidden className="size-4" />
        </span>
      )}
    </span>
  );
}
