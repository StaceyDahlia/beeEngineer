(function () {
  "use strict";

  const SKILL_LABELS = {
    local: "Локальные работы",
    connect: "Работы на подключение и дозаказы",
    emergency: "Аварийные работы",
  };

  const VEHICLE_LABELS = {
    "ОТ": "Общественный транспорт",
  };

  const SCENARIO_LABELS = {
    east: "Восток",
    southeast: "Юго-восток",
    southcenter: "Югоцентр",
  };

  const ROUTE_COLORS = [
    "#FFC800", "#72B7FF", "#64D98B", "#FF8A65", "#B692FF", "#34D6C5",
    "#FF6FAE", "#A8D65E", "#FFB454", "#5ED0FF", "#D596FF", "#8FB3FF",
  ];

  function stableRouteColor(id) {
    let hash = 2166136261;
    for (const char of String(id || "engineer")) {
      hash ^= char.charCodeAt(0);
      hash = Math.imul(hash, 16777619);
    }
    return ROUTE_COLORS[Math.abs(hash) % ROUTE_COLORS.length];
  }

  function hhmm(value) {
    if (!value) return "";
    const text = String(value);
    const match = text.match(/T(\d{2}:\d{2})/);
    return match ? match[1] : text.slice(0, 5);
  }

  function minute(value) {
    const time = hhmm(value);
    const parts = time.split(":").map(Number);
    return parts.length === 2 && parts.every(Number.isFinite)
      ? parts[0] * 60 + parts[1]
      : 0;
  }

  function engineerView(engineer) {
    return {
      id: String(engineer.id),
      name: engineer.name,
      skills: (engineer.skills || []).map((skill) => SKILL_LABELS[skill] || skill),
      vehicle: VEHICLE_LABELS[engineer.vehicle] || engineer.vehicle,
      shiftStart: hhmm(engineer.shift_start),
      shiftEnd: hhmm(engineer.shift_end),
      startPoint: engineer.start_point,
      status: engineer.status,
      equipment: Array.isArray(engineer.equipment) ? engineer.equipment : Object.keys(engineer.equipment || {}),
      equipmentSource: engineer.equipment_source || null,
      color: engineer.color || stableRouteColor(engineer.id),
    };
  }

  function jobView(job) {
    const title = job.subtype ? `${job.type} · ${job.subtype}` : job.type;
    const planned = job.service_start
      ? {
          arrivalMin: minute(job.arrival_time),
          startMin: minute(job.service_start),
          finishMin: minute(job.service_end),
          waitingMin: Number(job.waiting_min || 0),
        }
      : null;
    return {
      id: String(job.id),
      title,
      address: job.address,
      coords: job.coords,
      duration: Number(job.duration_min || 0),
      windowStart: hhmm(job.window_start),
      windowEnd: hhmm(job.window_end),
      priority:
        job.priority === "Срочная" ||
        job.priority_class === "emergency" ||
        job.skill === "emergency"
          ? "urgent"
          : "normal",
      priorityClass: job.priority_class || null,
      requiredSkill: SKILL_LABELS[job.skill] || job.skill,
      requiredVehicle: VEHICLE_LABELS[job.required_vehicle] || job.required_vehicle || "",
      requiredEquipment: Array.isArray(job.required_equipment) ? job.required_equipment : Object.keys(job.required_equipment || {}),
      status: job.status,
      engineerId: job.assigned_engineer_id,
      planned,
      apiExplanation: job.explanation || job.unassigned_reason?.message || "",
      assignmentExplanation: job.assignment_explanation || null,
      unassignedReason: job.unassigned_reason || null,
      replanPriorityImpact: job.replan_priority_impact || null,
    };
  }

  function adapt(response) {
    const engineers = (response.engineers || []).map(engineerView);
    const jobs = (response.jobs || []).map(jobView);
    const engineerById = new Map(engineers.map((engineer) => [engineer.id, engineer]));
    const jobById = new Map(jobs.map((job) => [job.id, job]));
    const reasons = {};
    jobs.forEach((job) => {
      if (job.unassignedReason) reasons[job.id] = [job.unassignedReason.message];
    });

    const planEngineers = (response.routes || []).map((route) => {
      const engineer = engineerById.get(String(route.engineer_id));
      const items = (route.stops || []).map((stop) => {
        const job = jobById.get(String(stop.job_id));
        return {
          ...job,
          order: Number(stop.sequence),
          travelMin: Number(stop.travel_min_from_prev || 0),
          arrivalMin: minute(stop.arrival_time),
          startMin: minute(stop.service_start),
          finishMin: minute(stop.service_end),
          waitingMin: Number(stop.waiting_min || 0),
        };
      });
      return {
        engineer,
        items,
        distance: Number(route.distance_km || 0),
        route: [engineer.startPoint, ...items.map((item) => item.coords)],
        routeSource: "Backend · статическая матрица",
        routeMetricSource: "backend_static",
      };
    });

    const flatJobs = [];
    planEngineers.forEach((entry) => {
      entry.items.forEach((item) => {
        flatJobs.push({
          jobId: item.id,
          engineerId: entry.engineer.id,
          arrivalMin: item.arrivalMin,
          startMin: item.startMin,
          finishMin: item.finishMin,
          travelMin: item.travelMin,
          waitingMin: item.waitingMin,
          order: item.order,
        });
      });
    });

    const metrics = response.metrics || {};
    const plan = {
      engineers: planEngineers,
      jobs: flatJobs,
      assigned: Number(metrics.assigned_count || 0),
      unassigned: Number(metrics.unassigned_count || 0),
      usedEngineers: Number(metrics.used_engineers ?? metrics.engineers_used ?? 0),
      totalDistance: Number(metrics.total_distance_km || 0),
      totalTravelMin: Number(metrics.total_travel_min || 0),
      totalWaitingMin: Number(metrics.total_waiting_min || 0),
      reasons,
      engineerDistances: metrics.per_engineer_distance_km || {},
      backend: {
        engine: response.engine,
        solverStatus: response.solver_status,
        solverStatusDetail: response.solver_status_detail,
        solveTimeMs: Number(response.solve_time_ms || 0),
        warnings: response.warnings || [],
        travelModel: response.travel_model,
        planId: response.plan_id || null,
        version: Number(response.version || 0),
        parentPlanId: response.parent_plan_id || null,
        diff: response.diff || null,
        event: response.event || null,
      },
    };

    const baselineMetrics = response.comparison?.baseline?.metrics || response.baseline_metrics || metrics;
    const baseline = JSON.parse(JSON.stringify(plan));
    baseline.assigned = Number(baselineMetrics.assigned_count || 0);
    baseline.unassigned = Number(baselineMetrics.unassigned_count || 0);
    baseline.usedEngineers = Number(
      baselineMetrics.used_engineers ?? baselineMetrics.engineers_used ?? 0
    );
    baseline.totalDistance = Number(baselineMetrics.total_distance_km || 0);
    baseline.totalTravelMin = Number(baselineMetrics.total_travel_min || 0);
    baseline.totalWaitingMin = Number(baselineMetrics.total_waiting_min || 0);
    baseline.engineerDistances = baselineMetrics.per_engineer_distance_km || {};
    baseline.backend = {
      engine: "baseline_greedy",
      solverStatus: response.comparison?.baseline?.solver_status || "feasible",
      solveTimeMs: Number(response.comparison?.baseline?.solve_time_ms || 0),
      warnings: response.comparison?.baseline?.warnings || [],
      travelModel: response.comparison?.baseline?.travel_model || response.travel_model,
    };

    return {
      jobs,
      engineers,
      plan,
      baseline,
      date: response.planning_date,
      branch: SCENARIO_LABELS[response.scenario_id] || response.scenario_id,
      planId: response.plan_id || null,
      version: Number(response.version || 0),
      parentPlanId: response.parent_plan_id || null,
      diff: response.diff || null,
      event: response.event || null,
    };
  }

  window.BeelinePlanAdapter = { adapt };
})();
