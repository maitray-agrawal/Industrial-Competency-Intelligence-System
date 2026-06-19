import React from 'react';
import { UnifiedSearchEntity } from '../types/UnifiedSearchEntity';

interface UnifiedEntityDetailsProps {
  entity: UnifiedSearchEntity;
}

const PillBadge: React.FC<{ label: string }> = ({ label }) => (
  <span className="inline-flex items-center rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1 text-xs font-semibold text-emerald-700">
    {label}
  </span>
);

export const UnifiedEntityDetails: React.FC<UnifiedEntityDetailsProps> = ({ entity }) => {
  const hasLearningFootprint = Array.isArray(entity.learningFootprint) && entity.learningFootprint.length > 0;
  const hasSyllabusBreakdown = entity.syllabusBreakdown && entity.syllabusBreakdown.subTopics.length > 0;

  return (
    <div className="space-y-6 p-6">
      <section className="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm">
        <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <p className="text-sm font-semibold uppercase tracking-[0.2em] text-slate-500">{entity.entityType.replace('_', ' ')}</p>
            <h1 className="mt-2 text-2xl font-semibold text-slate-900">{entity.name}</h1>
          </div>
          {entity.entityType === 'THEORY_TOPIC' && entity.metadata.academicContext ? (
            <div className="grid gap-2 sm:grid-cols-3">
              <PillBadge label={entity.metadata.academicContext.courseName} />
              <PillBadge label={entity.metadata.academicContext.semester} />
              <PillBadge label={entity.metadata.academicContext.unitClassification} />
            </div>
          ) : null}
        </div>

        <div className="rounded-3xl bg-slate-50 px-5 py-4 text-sm leading-6 text-slate-700">
          {entity.metadata.description}
        </div>
      </section>

      {entity.entityType === 'THEORY_TOPIC' ? (
        <section className="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm">
          <div className="mb-5 flex items-center justify-between">
            <div>
              <h2 className="text-xl font-semibold text-slate-900">Where to Learn in the Plant</h2>
              <p className="text-sm text-slate-500">Mapped plant stations and active tools that demonstrate this theory.</p>
            </div>
          </div>

          {hasLearningFootprint ? (
            <div className="grid gap-4">
              {entity.learningFootprint.map((item) => (
                <article key={`${item.shopName}-${item.stationNo}`} className="overflow-hidden rounded-3xl border border-slate-200 bg-slate-50 p-5 shadow-sm">
                  <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                    <div>
                      <p className="text-sm font-semibold uppercase tracking-[0.16em] text-slate-500">{item.shopName}</p>
                      <h3 className="mt-2 text-lg font-semibold text-slate-900">Station {item.stationNo}</h3>
                    </div>
                    <span className="inline-flex items-center rounded-full bg-slate-900 px-3 py-1 text-sm font-semibold text-white shadow-sm">
                      {item.stationNo}
                    </span>
                  </div>

                  <div className="space-y-3 rounded-3xl bg-white p-4 shadow-sm">
                    <div>
                      <p className="text-xs uppercase tracking-[0.18em] text-slate-500">Real-World Application</p>
                      <p className="mt-2 text-sm leading-6 text-slate-700">{item.practicalOperation}</p>
                    </div>

                    <div>
                      <p className="text-xs uppercase tracking-[0.18em] text-slate-500">Active Tools</p>
                      <div className="mt-3 flex flex-wrap gap-2">
                        {item.associatedTools.length > 0 ? (
                          item.associatedTools.map((tool) => (
                            <span key={tool} className="inline-flex items-center rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1 text-xs font-semibold text-emerald-700">
                              {tool}
                            </span>
                          ))
                        ) : (
                          <span className="text-sm text-slate-500">No physical tools mapped yet.</span>
                        )}
                      </div>
                    </div>
                  </div>
                </article>
              ))}
            </div>
          ) : (
            <div className="rounded-3xl border border-dashed border-slate-300 bg-slate-50 p-6 text-center text-sm text-slate-600">
              No physical shop floor deployments mapped to this theoretical module yet.
            </div>
          )}
        </section>
      ) : null}

      {entity.syllabusBreakdown && Array.isArray(entity.syllabusBreakdown.subTopics) ? (
        <section className="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm">
          <h2 className="text-xl font-semibold text-slate-900">Syllabus Breakdown</h2>
          <p className="mt-2 text-sm text-slate-500">Subtopics and curriculum structure for this parent topic.</p>
          <div className="mt-4 space-y-3">
            <div className="rounded-3xl bg-slate-50 p-4">
              <p className="text-sm font-semibold text-slate-700">Parent Topic</p>
              <p className="mt-2 text-slate-900">{entity.syllabusBreakdown.parentTopic}</p>
            </div>
            <div className="grid gap-2 sm:grid-cols-2">
              {entity.syllabusBreakdown.subTopics.map((subTopic) => (
                <div key={subTopic} className="rounded-3xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-700">
                  {subTopic}
                </div>
              ))}
            </div>
          </div>
        </section>
      ) : null}

      {!hasLearningFootprint && entity.entityType === 'THEORY_TOPIC' ? (
        <div className="rounded-3xl border border-dashed border-slate-300 bg-slate-50 p-6 text-center text-sm text-slate-600">
          No physical shop floor deployments mapped to this theoretical module yet.
        </div>
      ) : null}
    </div>
  );
};
