import { create } from "zustand";

type InspectorTab = "timeline" | "conversation" | "raw" | "memory" | "metrics";

type UiState = {
  inspectorTab: InspectorTab;
  timelineFilter: string;
  setInspectorTab: (tab: InspectorTab) => void;
  setTimelineFilter: (filter: string) => void;
};

export const useUiStore = create<UiState>((set) => ({
  inspectorTab: "timeline",
  timelineFilter: "all",
  setInspectorTab: (inspectorTab) => set({ inspectorTab }),
  setTimelineFilter: (timelineFilter) => set({ timelineFilter }),
}));
