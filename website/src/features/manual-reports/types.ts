// Shapes of /manual-reports (requirements 15.6; decision D-013). Reports are written by Guards in
// the mobile app; they are a separate record from AI alerts.

export interface ManualReport {
  id: string;
  guard_id: string;
  guard_name: string;
  report_type: string;
  description: string;
  building_id: string | null;
  building_name: string;
  floor_id: string | null;
  floor_name: string | null;
  area_id: string | null;
  area_name: string | null;
  has_photo: boolean;
  occurred_at: string | null;
  status: "SUBMITTED" | "REVIEWED";
  review_note: string | null;
  reviewed_at: string | null;
  created_at: string;
}
