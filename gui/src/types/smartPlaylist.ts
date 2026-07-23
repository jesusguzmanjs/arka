export type SmartPlaylistField =
  | "bpm"
  | "playcount"
  | "genre"
  | "label"
  | "comment"
  | "key"
  | "import_date"
  | "last_played"
  | "rating";

export type SmartPlaylistOperator =
  | "equals"
  | "greater_than"
  | "less_than"
  | "between"
  | "contains"
  | "is_exactly"
  | "does_not_contain"
  | "in_last_days"
  | "before"
  | "after"
  | "greater_than_or_equal"
  | "less_than_or_equal";

export interface SmartPlaylistRule {
  field: SmartPlaylistField;
  operator: SmartPlaylistOperator;
  value: string | number | { min: number; max: number };
}

export interface SmartPlaylistPayload {
  name: string;
  match: "all" | "any";
  rules: SmartPlaylistRule[];
}

export interface SmartPlaylistCompileResult {
  type: "smart_playlist_compiled";
  name: string;
  matched: number;
  uuid: string;
}
