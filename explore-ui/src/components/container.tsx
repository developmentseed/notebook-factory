import type { ComponentProps } from "react"

import { cn } from "@/lib/utils"

/** Centered, max-width wrapper so layouts stay readable on wide screens. */
export function Container({ className, ...props }: ComponentProps<"div">) {
  return <div className={cn("container", className)} {...props} />
}

/** 12-column grid to lay out content inside a Container. */
export function Grid({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      className={cn(
        "grid grid-cols-4 gap-6 md:grid-cols-8 lg:grid-cols-12",
        className
      )}
      {...props}
    />
  )
}
