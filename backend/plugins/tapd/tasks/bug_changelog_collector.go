/*
Licensed to the Apache Software Foundation (ASF) under one or more
contributor license agreements.  See the NOTICE file distributed with
this work for additional information regarding copyright ownership.
The ASF licenses this file to You under the Apache License, Version 2.0
(the "License"); you may not use this file except in compliance with
the License.  You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
*/

package tasks

import (
	"fmt"
	"github.com/apache/incubator-devlake/core/errors"
	"github.com/apache/incubator-devlake/core/plugin"
	helper "github.com/apache/incubator-devlake/helpers/pluginhelper/api"
	"net/url"
)

const RAW_BUG_CHANGELOG_TABLE = "tapd_api_bug_changelogs"

var _ plugin.SubTaskEntryPoint = CollectBugChangelogs

func CollectBugChangelogs(taskCtx plugin.SubTaskContext) errors.Error {
	rawDataSubTaskArgs, data := CreateRawDataSubTaskArgs(taskCtx, RAW_BUG_CHANGELOG_TABLE, false)
	logger := taskCtx.GetLogger()
	logger.Info("collect storyChangelogs")
	collectorWithState, err := helper.NewStatefulApiCollector(*rawDataSubTaskArgs, data.TimeAfter)
	if err != nil {
		return err
	}
	incremental := collectorWithState.IsIncremental()

	err = collectorWithState.InitCollector(helper.ApiCollectorArgs{
		Incremental: incremental,
		ApiClient:   data.ApiClient,
		PageSize:    100,
		UrlTemplate: "bug_changes",
		Query: func(reqData *helper.RequestData) (url.Values, errors.Error) {
			query := url.Values{}
			query.Set("workspace_id", fmt.Sprintf("%v", data.Options.WorkspaceId))
			query.Set("page", fmt.Sprintf("%v", reqData.Pager.Page))
			query.Set("limit", fmt.Sprintf("%v", reqData.Pager.Size))
			query.Set("order", "created desc")
			if data.TimeAfter != nil {
				query.Set("created",
					fmt.Sprintf(">%s",
						data.TimeAfter.In(data.Options.CstZone).Format("2006-01-02")))
			}
			if incremental {
				query.Set("created",
					fmt.Sprintf(">%s",
						collectorWithState.LatestState.LatestSuccessStart.In(data.Options.CstZone).Format("2006-01-02")))
			}
			return query, nil
		},
		ResponseParser: GetRawMessageArrayFromResponse,
	})
	if err != nil {
		logger.Error(err, "collect story changelog error")
		return err
	}
	return collectorWithState.Execute()
}

var CollectBugChangelogMeta = plugin.SubTaskMeta{
	Name:             "collectBugChangelogs",
	EntryPoint:       CollectBugChangelogs,
	EnabledByDefault: true,
	Description:      "collect Tapd bugChangelogs",
	DomainTypes:      []string{plugin.DOMAIN_TYPE_TICKET},
}
