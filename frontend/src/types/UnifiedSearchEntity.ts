export interface UnifiedSearchEntity {
  id: string;
  name: string;
  entityType: 'THEORY_TOPIC' | 'TOOL' | 'STATION';
  metadata: {
    description: string;
    academicContext?: {
      courseName: string;
      semester: string;
      unitClassification: string;
    };
  };
  learningFootprint: Array<{
    shopName: string;
    stationNo: string;
    associatedTools: string[];
    practicalOperation: string;
  }>;
  syllabusBreakdown?: {
    parentTopic: string;
    subTopics: string[];
  };
}
